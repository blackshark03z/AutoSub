import re


_URL_IN_TEXT = re.compile(r"https?://[^\s'\"]+")

SENSITIVE_WORDS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "AUTHORIZATION",
    "AUTH",
    "SIG",
    "SIGNATURE",
    "CREDENTIAL",
    "SESSDATA",
    "BILI_JCT",
)
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
    """Sanitize URL by masking tokens, keys, userinfo credentials, and sensitive parameters."""
    if not url:
        return ""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            userinfo, host = netloc.split("@", 1)
            if ":" in userinfo:
                user, _ = userinfo.split(":", 1)
                netloc = f"{user}:<redacted>@{host}"
            else:
                netloc = f"<redacted>@{host}"

        new_query = ""
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=True)
            redacted_qs = {}
            for k, v in qs.items():
                if any(w in k.upper() for w in SENSITIVE_WORDS):
                    redacted_qs[k] = ["<redacted>"]
                else:
                    redacted_qs[k] = [str(redact(item)) for item in v]
            new_query = urlencode(redacted_qs, doseq=True)

        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    except Exception:
        return str(redact(url))


def canonical_url(url: str) -> str:
    """Return only the safe canonical identifier: scheme://host/path (no query/fragment).

    Share/session identifiers (``mid``, ``buvid``, ``share_session_id``, ``timestamp``,
    ``unique_k`` …) live in the query string and are dropped entirely so no resolved
    share URL can be reconstructed from a manifest or log.
    """
    if not url:
        return ""
    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.split("@")[-1]
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except Exception:
        return ""


def redact_text(text: str) -> str:
    """Redact secret patterns and strip every embedded URL down to its canonical form.

    Log lines (yt-dlp stderr/stdout) frequently echo the resolved share URL, whose
    query string carries ``mid``, ``buvid``, ``share_session_id``, ``timestamp`` and
    tokens. For free text we drop the whole query/fragment, keeping only
    ``scheme://host/path`` so no share URL can be reconstructed from logs or manifests.
    """
    if not text:
        return text
    redacted = str(redact(text))
    return _URL_IN_TEXT.sub(lambda m: canonical_url(m.group(0)) or "<redacted-url>", redacted)
