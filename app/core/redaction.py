import re


SENSITIVE_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION")
SECRET_VALUE_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk_[0-9A-Za-z_\-]{16,}"),
)


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {k: ("<redacted>" if any(word in k.upper() for word in SENSITIVE_WORDS) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in SECRET_VALUE_PATTERNS:
            redacted = pattern.sub("<redacted>", redacted)
        return redacted
    return value


def redact_url(url: str) -> str:
    """Sanitize URL by masking tokens, keys, and credentials."""
    if not url:
        return ""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return str(redact(url))
        qs = parse_qs(parsed.query, keep_blank_values=True)
        redacted_qs = {}
        for k, v in qs.items():
            if any(w in k.upper() for w in SENSITIVE_WORDS):
                redacted_qs[k] = ["<redacted>"]
            else:
                redacted_qs[k] = [str(redact(item)) for item in v]
        new_query = urlencode(redacted_qs, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    except Exception:
        return str(redact(url))

