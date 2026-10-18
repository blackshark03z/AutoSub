"""Offline Chinese localization adapter backed by Tencent HY-MT2 GGUF on llama.cpp.

Work package: opencode_deepseek_e2_c4_longrun_wp_2026-10-17 (unblock C4).

Scope (narrow, fail-closed):
- ``zh`` -> ``en-US`` and ``zh`` -> ``vi-VN`` for embedded-caption cues produced by
  the OCR/correction lane, preserving ``cue_id`` and ``start_ms``/``end_ms`` exactly.
- Real local inference only. No cloud translation, no paid API fallback.
- Deterministic provenance: model SHA-256, runtime identity, prompt digest,
  decoding parameters, and stable seed.

This module intentionally does not modify the existing
``app.services.experimental_zh_vi_adapter`` contract. It reuses the cue shape and
adds bilingual locales, locale fail-closed behavior, and a corrected quality gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

PROMPT_VERSION = "hy-mt2-official-default-2026-05-20"
HY_MT2_PROMPT_TEMPLATE = (
    "Translate the following text into {target_lang}. Note that you should "
    "only output the translated result without any additional explanation:\n\n"
    "{source_text}"
)

# Full language names are required by the model card; BCP-47 locale tags are the
# product contract and are kept separately from the model-facing language name.
SUPPORTED_LOCALES: Mapping[str, str] = {
    "en-US": "English",
    "vi-VN": "Vietnamese",
}

GLOSSARY_VERSIONS: Mapping[str, str] = {
    "en-US": "glossary-en-US-v1",
    "vi-VN": "glossary-vi-VN-v1",
}

GLOSSARY_EN_US: Mapping[str, str] = {
    "万法宗": "Wanfa Sect",
    "镇宗功法": "sect-guarding technique",
    "房术大全": "Manual of Bedroom Arts",
    "大限已至": "my time has come",
}

GLOSSARY_VI_VN: Mapping[str, str] = {
    "万法宗": "Vạn Pháp Tông",
    "镇宗功法": "công pháp trấn tông",
    "房术大全": "Phòng Thuật Đại Toàn",
    "大限已至": "đại hạn đã đến",
}

_GLOSSARIES: Mapping[str, Mapping[str, str]] = {
    "en-US": GLOSSARY_EN_US,
    "vi-VN": GLOSSARY_VI_VN,
}

# Phase 2 (OD-03 part B) source-evidence grounded context glossary.  Keys are the
# authentic OCR source terms; values are the required target term.  Kept separate
# from GLOSSARY_EN_US/VI_VN so the C4 default contract (and its prompt digest)
# is unchanged.
CONTEXT_GLOSSARY_EN_US: Mapping[str, str] = {
    "徒儿": "disciple",
    "万法宗": "Wanfa Sect",
    "宗门": "sect",
    "镇宗功法": "signature sect-guarding technique",
    "房术大全": "Manual of Bedchamber Arts",
    "运动大全": "Manual of Exercises",
    "大限已至": "my time has come",
    "嘎": "to die",
}

CONTEXT_GLOSSARY_VI_VN: Mapping[str, str] = {
    "徒儿": "đồ nhi",
    "万法宗": "Vạn Pháp Tông",
    "宗门": "tông môn",
    "镇宗功法": "công pháp trấn tông",
    "房术大全": "Phòng Thuật Đại Toàn",
    "运动大全": "Bí kíp vận động",
    "大限已至": "đại hạn đã đến",
    "嘎": "chết (khẩu ngữ)",
}

CONTEXT_GLOSSARIES: Mapping[str, Mapping[str, str]] = {
    "en-US": CONTEXT_GLOSSARY_EN_US,
    "vi-VN": CONTEXT_GLOSSARY_VI_VN,
}

CONTEXT_GLOSSARY_VERSIONS: Mapping[str, str] = {
    "en-US": "glossary-en-US-v2-context",
    "vi-VN": "glossary-vi-VN-v2-context",
}

# Deterministic rule post-edit: (source marker, [(pattern, replacement)]).  Applied
# only when the marker is present in the source cue.  Case-insensitive regex.
RULE_POSTEDIT_EN_US: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("万法宗", ((r"wana\s+fa\s+sect", "Wanfa Sect"), (r"wan\s+fa\s+sect", "Wanfa Sect"))),
    ("徒儿", ((r"\bapprentice\b", "disciple"),)),
    ("房术大全", ((r"comprehensive guide on housekeeping", "Manual of Bedchamber Arts"),
                 (r"\bhousekeeping\b", "bedchamber arts"))),
    ("镇宗功法", ((r"town chief'?s skillful technique", "signature sect-guarding technique"),
                 (r"\btown chief\b", "sect"))),
    ("嘎", ((r"\s*\([^)]*\)\s*$", ""),)),
)
RULE_POSTEDIT_VI_VN: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("万法宗", ((r"tông vạn pháp", "Vạn Pháp Tông"), (r"vạn pháp tông", "Vạn Pháp Tông"))),
    ("徒儿", ((r"\btu[ếe]r\b", "đồ nhi"), (r"\btuer\b", "đồ nhi"))),
    ("房术大全", ((r"kiến trúc nhà ở", "Phòng Thuật Đại Toàn"), (r"nhà cửa", "Phòng Thuật Đại Toàn"))),
    ("镇宗功法", ((r"phương pháp công phu[^.,;!?]*", "công pháp trấn tông"),)),
)
RULE_POSTEDIT: Mapping[str, tuple[tuple[str, tuple[tuple[str, str], ...]], ...]] = {
    "en-US": RULE_POSTEDIT_EN_US,
    "vi-VN": RULE_POSTEDIT_VI_VN,
}

CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class HyMt2Error(RuntimeError):
    """Base error for the HY-MT2 offline localization adapter."""


class UnsupportedLocaleError(HyMt2Error):
    """Raised when a locale has no verified offline target language mapping."""


def resolve_target_language(locale: str) -> str:
    """Map a BCP-47 locale to the HY-MT2 full language name, fail-closed."""
    key = str(locale or "").strip()
    if key not in SUPPORTED_LOCALES:
        raise UnsupportedLocaleError(
            f"Locale '{key}' is not supported by the offline C4 adapter; "
            f"supported locales: {sorted(SUPPORTED_LOCALES)}"
        )
    return SUPPORTED_LOCALES[key]


def build_translation_prompt(source_text: str, locale: str) -> str:
    """Build the official Hy-MT2 default translation prompt for a locale."""
    target_lang = resolve_target_language(locale)
    return HY_MT2_PROMPT_TEMPLATE.format(target_lang=target_lang, source_text=source_text)


def build_contextual_translation_prompt(
    source_text: str,
    locale: str,
    *,
    context_before: str | None = None,
    context_after: str | None = None,
    glossary: Mapping[str, str] | None = None,
) -> str:
    """Build a context-enriched, glossary-constrained prompt (OD-03 part B).

    Only glossary terms actually present in the current line or its neighbours are
    injected, so short cues are not padded with irrelevant terminology.
    """
    target_lang = resolve_target_language(locale)
    terms = dict(glossary if glossary is not None else CONTEXT_GLOSSARIES.get(locale, {}))
    haystack = " ".join(
        part for part in (source_text, context_before or "", context_after or "") if part
    )
    relevant = [f"- {term} = {preferred}" for term, preferred in terms.items() if term in haystack]
    glossary_block = ("Terminology to use exactly:\n" + "\n".join(relevant) + "\n\n") if relevant else ""
    context_lines = []
    if context_before:
        context_lines.append(f"Previous line: {context_before}")
    if context_after:
        context_lines.append(f"Next line: {context_after}")
    context_block = ("Story context (do not translate)\n" + "\n".join(context_lines) + "\n\n") if context_lines else ""
    return (
        f"Translate the following Chinese subtitle line into {target_lang}. "
        "Only output the translated result without any additional explanation, "
        "preserving the story meaning and tone.\n\n"
        f"{glossary_block}{context_block}Line to translate:\n{source_text}"
    )


def apply_rule_postedit(source_text: str, locale: str, translated_text: str) -> tuple[str, list[str]]:
    """Deterministically enforce required terminology after model inference.

    Returns the (possibly edited) text and the list of applied rule ids.  Rules are
    only applied when their source marker appears in the source cue, so an edit is
    always traceable to source evidence.
    """
    text = str(translated_text or "")
    applied: list[str] = []
    for marker, rules in RULE_POSTEDIT.get(locale, ()):  # type: ignore[arg-type]
        if marker not in source_text:
            continue
        for pattern, replacement in rules:
            new_text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
            if new_text != text:
                applied.append(f"{marker}:{pattern}->{replacement}")
                text = new_text
    return text, applied


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_translation(text: str) -> str:
    """Strip wrapper quotes/whitespace artifacts without altering content."""
    cleaned = str(text or "").strip()
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (
        cleaned.startswith("'") and cleaned.endswith("'")
    ):
        cleaned = cleaned[1:-1].strip()
    cleaned = " ".join(cleaned.split())
    return cleaned


def detect_translation_defects(
    source_text: str, translated_text: str
) -> tuple[bool, str | None]:
    """Detect hard defects (defects, not stylistic concerns).

    - empty output
    - CJK leakage (model failed to translate)
    - 3x repetition loop
    - gross length explosion relative to a word/character budget suitable for
      two-line subtitles (the earlier character-ratio check produced false
      positives on short CJK cues, so a budget in output characters is used).
    """
    text = str(translated_text or "").strip()
    if not text:
        return True, "EMPTY_OUTPUT"

    cjk = CJK_PATTERN.findall(text)
    if cjk:
        return True, f"CJK_LEAKAGE: found {len(cjk)} untranslated characters"

    words = text.split()
    if len(words) >= 6:
        for n in (1, 2, 3):
            for index in range(len(words) - 3 * n + 1):
                first = " ".join(words[index : index + n])
                second = " ".join(words[index + n : index + 2 * n])
                third = " ".join(words[index + 2 * n : index + 3 * n])
                if first == second == third:
                    return True, f"REPETITION_LOOP: '{first}'"

    # Two-line subtitle budget: allow generous but bounded growth for very short
    # CJK source cues while still catching hallucinated storytelling.
    source_len = max(len(source_text.strip()), 1)
    budget = max(120, source_len * 8)
    if len(text) > budget:
        return True, f"LENGTH_EXPLOSION: {len(text)} chars over budget {budget}"

    return False, None


def glossary_adherence(locale: str, source_text: str, translated_text: str) -> dict[str, str]:
    """Best-effort terminology report (informational, never rewrites output)."""
    glossary = _GLOSSARIES.get(locale, {})
    report: dict[str, str] = {}
    lowered = translated_text.casefold()
    for term, preferred in glossary.items():
        if term in source_text:
            report[term] = "MATCH" if preferred.casefold() in lowered else "UNMATCHED"
    return report


@dataclass(frozen=True)
class HyMt2RuntimeProvenance:
    runtime: str
    runtime_version: str | None
    model_path: str | None
    model_sha256: str | None
    prompt_version: str
    transport: str
    decode_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalizedCue:
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str
    target_locale: str
    translation: str | None
    status: str
    confidence: float
    provenance: str
    model_sha256: str | None = None
    model_path: str | None = None
    prompt_sha256: str | None = None
    translation_sha256: str | None = None
    elapsed_s: float | None = None
    glossary_version: str | None = None
    glossary: dict[str, str] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_cue(cue: Any) -> tuple[str, int, int, str]:
    if isinstance(cue, Mapping):
        return (
            str(cue["cue_id"]),
            int(cue["start_ms"]),
            int(cue["end_ms"]),
            str(cue["zh_text"]),
        )
    return (str(cue.cue_id), int(cue.start_ms), int(cue.end_ms), str(cue.zh_text))


class HyMt2OfflineLocalizer:
    """Fail-closed offline localizer using a HY-MT2 GGUF on llama.cpp."""

    def __init__(
        self,
        *,
        server_endpoint: str | None = None,
        cli_path: Path | str | None = None,
        model_path: Path | str | None = None,
        runtime_version: str | None = None,
        threads: int = 6,
        n_predict: int = 256,
        seed: int = 42,
        timeout: float = 120.0,
        infer_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.server_endpoint = server_endpoint
        self.cli_path = Path(cli_path) if cli_path else None
        self.model_path = Path(model_path) if model_path else self._discover_model_path()
        self.runtime_version = runtime_version
        self.threads = threads
        self.n_predict = n_predict
        self.seed = seed
        self.timeout = timeout
        self._infer_fn = infer_fn
        self._model_sha256: str | None = None
        self._decode_params = {
            "temperature": 0.7,
            "top_p": 0.6,
            "top_k": 20,
            "repeat_penalty": 1.05,
            "n_predict": n_predict,
            "seed": seed,
        }

    @staticmethod
    def _discover_model_path() -> Path | None:
        env_path = os.environ.get("AUTOSUB_HY_MT2_MODEL_PATH", "").strip()
        if env_path and Path(env_path).is_file():
            return Path(env_path)
        candidates = [
            Path("models/Hy-MT2-1.8B-Q4_K_M.gguf"),
            Path("models/hy-mt2-1.8b-q4.gguf"),
            Path("scratch/e2_runtime/models/Hy-MT2-1.8B-Q4_K_M.gguf"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    @property
    def model_sha256(self) -> str | None:
        if self._model_sha256 is None and self.model_path and self.model_path.is_file():
            self._model_sha256 = sha256_file(self.model_path)
        return self._model_sha256

    @property
    def transport(self) -> str | None:
        if self._infer_fn is not None:
            return "injected"
        if self.server_endpoint:
            return "server"
        if self.cli_path and self.model_path and self.model_path.is_file():
            return "cli"
        return None

    @property
    def is_runtime_available(self) -> bool:
        if self._infer_fn is not None:
            return True
        if self.server_endpoint:
            return True
        return bool(self.cli_path and self.model_path and self.model_path.is_file())

    def provenance(self) -> HyMt2RuntimeProvenance:
        return HyMt2RuntimeProvenance(
            runtime="llama.cpp",
            runtime_version=self.runtime_version,
            model_path=str(self.model_path) if self.model_path else None,
            model_sha256=self.model_sha256,
            prompt_version=PROMPT_VERSION,
            transport=self.transport or "unavailable",
            decode_params=dict(self._decode_params),
        )

    def translate_cue(self, cue: Any, locale: str) -> LocalizedCue:
        cue_id, start_ms, end_ms, zh_text = normalize_cue(cue)

        def result(**kwargs: Any) -> LocalizedCue:
            base = dict(
                cue_id=cue_id,
                start_ms=start_ms,
                end_ms=end_ms,
                source_text=zh_text,
                target_locale=locale,
                model_sha256=self.model_sha256,
                model_path=str(self.model_path) if self.model_path else None,
                glossary_version=GLOSSARY_VERSIONS.get(locale),
            )
            base.update(kwargs)
            return LocalizedCue(**base)

        if locale not in SUPPORTED_LOCALES:
            return result(
                translation=None,
                status="UNSUPPORTED_LOCALE",
                confidence=0.0,
                provenance="none",
                error_message=f"Unsupported locale '{locale}'; C4 output is fail-closed",
            )

        if not zh_text.strip():
            return result(translation="", status="EMPTY_SOURCE", confidence=1.0, provenance="source_empty")

        if not self.is_runtime_available:
            return result(
                translation=None,
                status="RUNTIME_UNAVAILABLE",
                confidence=0.0,
                provenance="none",
                error_message=(
                    "Offline HY-MT2 runtime not configured (no server endpoint, no "
                    "llama.cpp CLI + model). C4 remains BLOCKED; paid API fallback is forbidden."
                ),
            )

        prompt = build_translation_prompt(zh_text, locale)
        prompt_sha = sha256_text(prompt)
        started = time.perf_counter()
        try:
            raw = self._infer(prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced as fail-closed status
            return result(
                translation=None,
                status="INFERENCE_ERROR",
                confidence=0.0,
                provenance=self.transport or "unavailable",
                prompt_sha256=prompt_sha,
                error_message=f"{type(exc).__name__}: {exc}",
            )
        elapsed = round(time.perf_counter() - started, 3)

        cleaned = sanitize_translation(raw)
        defective, reason = detect_translation_defects(zh_text, cleaned)
        if defective:
            return result(
                translation=None,
                status=f"QUALITY_REJECTED:{reason}",
                confidence=0.0,
                provenance=f"{self.transport}_offline",
                prompt_sha256=prompt_sha,
                translation_sha256=sha256_text(cleaned),
                elapsed_s=elapsed,
                error_message=reason,
            )

        return result(
            translation=cleaned,
            status="TRANSLATED",
            confidence=0.95,
            provenance=f"llama.cpp_hymt2_q4_{self.transport}_offline",
            prompt_sha256=prompt_sha,
            translation_sha256=sha256_text(cleaned),
            elapsed_s=elapsed,
            glossary=glossary_adherence(locale, zh_text, cleaned),
        )

    def translate_cues(self, cues: Iterable[Any], locale: str) -> list[LocalizedCue]:
        return [self.translate_cue(cue, locale) for cue in cues]

    def infer(self, prompt: str) -> str:
        """Public wrapper around the configured offline inference transport."""
        return self._infer(prompt)

    def _infer(self, prompt: str) -> str:
        if self._infer_fn is not None:
            return self._infer_fn(prompt)
        if self.server_endpoint:
            return self._infer_server(prompt)
        if self.cli_path:
            return self._infer_cli(prompt)
        raise HyMt2Error("No offline inference transport is configured")

    def _infer_server(self, prompt: str) -> str:
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            **self._decode_params,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.server_endpoint,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body)
        return parsed["choices"][0]["message"]["content"]

    def _infer_cli(self, prompt: str) -> str:
        if not self.model_path:
            raise HyMt2Error("Model path is required for CLI inference")
        command = [
            str(self.cli_path),
            "-m",
            str(self.model_path),
            "-p",
            prompt,
            "--jinja",
            "-ngl",
            "0",
            "-n",
            str(self.n_predict),
            "-t",
            str(self.threads),
            "-s",
            str(self.seed),
            "--temp",
            str(self._decode_params["temperature"]),
            "--top-p",
            str(self._decode_params["top_p"]),
            "--top-k",
            str(self._decode_params["top_k"]),
            "--repeat-penalty",
            str(self._decode_params["repeat_penalty"]),
            "-st",
            "--no-warmup",
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if completed.returncode != 0:
            raise HyMt2Error(f"llama.cpp CLI failed with exit code {completed.returncode}")
        return _extract_cli_response(completed.stdout, prompt)


def _extract_cli_response(stdout: str, prompt: str) -> str:
    """Best-effort extraction of the completion from llama-cli single-turn output."""
    text = _ANSI_PATTERN.sub("", stdout)
    marker = prompt.strip()
    if marker and marker in text:
        tail = text.split(marker, 1)[1]
    else:
        tail = text
    for stop in ("[ Prompt:", "Exiting...", "> "):
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return tail.strip().strip("─").strip()
