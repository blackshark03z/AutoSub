from __future__ import annotations

import re
import urllib.parse
from typing import Any

SENSITIVE_PARAM_PATTERNS = [
    re.compile(r"^(?:token|auth|key|apikey|api_key|secret|sig|sign|signature)$", re.IGNORECASE),
    re.compile(r"^(?:pass|password|pwd|credential|credentials)$", re.IGNORECASE),
    re.compile(r"^(?:session|sess|sessdata|bili_jct|ticket|bvid_ticket)$", re.IGNORECASE),
    re.compile(r"^(?:cookie|cookies|access_token|refresh_token|id_token)$", re.IGNORECASE),
    re.compile(r"^(?:wssecret|wstime|ckey|guid|qb_guid)$", re.IGNORECASE),
]

SENSITIVE_TEXT_PATTERNS = [
    (re.compile(r"(AIza[0-9A-Za-z_\-]{20,})"), "[REDACTED_API_KEY]"),
    (re.compile(r"((?:sk-|bearer\s+)[A-Za-z0-9_\-]{20,})", re.IGNORECASE), "[REDACTED_TOKEN]"),
    (re.compile(r"(SESSDATA=[A-Za-z0-9%_\-]+)", re.IGNORECASE), "SESSDATA=[REDACTED]"),
    (re.compile(r"(bili_jct=[A-Za-z0-9]+)", re.IGNORECASE), "bili_jct=[REDACTED]"),
]


def is_sensitive_param(name: str) -> bool:
    clean = name.strip()
    return any(pattern.match(clean) for pattern in SENSITIVE_PARAM_PATTERNS)


def redact_url(url: str) -> str:
    """Sanitize URL authority credentials and sensitive query parameters."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return "[INVALID_URL]"

    # 1. Redact authority password if present
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.split("@", 1)
        if ":" in userinfo:
            username, _ = userinfo.split(":", 1)
            netloc = f"{username}:[REDACTED]@{host}"
        else:
            netloc = f"[REDACTED]@{host}"

    # 2. Redact sensitive query parameters
    query_str = parts.query
    if query_str:
        qsl = urllib.parse.parse_qsl(query_str, keep_blank_values=True)
        sanitized_qsl: list[tuple[str, str]] = []
        for key, val in qsl:
            if is_sensitive_param(key):
                sanitized_qsl.append((key, "[REDACTED]"))
            else:
                sanitized_qsl.append((key, val))
        new_query = urllib.parse.urlencode(sanitized_qsl)
    else:
        new_query = ""

    redacted = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, new_query, parts.fragment))
    return redacted


def redact_text(text: str) -> str:
    """Redact secret-like strings and tokens from log messages and diagnostics."""
    if not text:
        return ""
    sanitized = text
    for pattern, replacement in SENSITIVE_TEXT_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized
