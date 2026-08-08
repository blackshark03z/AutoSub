from pathlib import Path


def load_secret_lines(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Secret file not found: {path.name}")
    return load_strict_secret_text(path.read_text(encoding="utf-8"), source_name=path.name)


def load_strict_secret_lines(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Secret file not found: {path.name}")
    return load_strict_secret_text(path.read_text(encoding="utf-8"), source_name=path.name)


def load_strict_secret_text(text: str, *, source_name: str = "secret text") -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("\ufeff"):
            stripped = stripped.removeprefix("\ufeff").strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _is_quoted(stripped):
            raise ValueError(f"Secret source contains a quoted entry: {source_name}")
        if "PLACEHOLDER" in stripped.upper() or stripped in {"YOUR_API_KEY", "TODO"}:
            raise ValueError(f"Secret source contains a placeholder entry: {source_name}")
        if any(character.isspace() for character in stripped):
            raise ValueError(f"Secret source contains embedded whitespace: {source_name}")
        if stripped not in seen:
            values.append(stripped)
            seen.add(stripped)
    if not values:
        raise ValueError(f"Secret source has no configured entries: {source_name}")
    return values


def _is_quoted(value: str) -> bool:
    return len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}
