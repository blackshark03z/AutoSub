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
