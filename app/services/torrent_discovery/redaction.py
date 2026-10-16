"""
AutoSub Cloud V2 — Secret and Passkey Redaction for BitTorrent Trackers
"""

import re
import urllib.parse
from typing import List

# Query parameters commonly containing sensitive user tokens or passkeys
SENSITIVE_PARAM_NAMES = {
    "passkey",
    "pass_key",
    "auth",
    "authkey",
    "auth_key",
    "token",
    "secret",
    "key",
    "torrent_pass",
    "uk",
    "userkey",
    "rsskey",
    "apikey",
    "api_key",
}

# Regex to detect 24+ hex or base32/base64 tokens in URL path segments
# e.g., /announce/4a8c9b.../ or /4a8c9b.../announce
PATH_TOKEN_PATTERN = re.compile(r"/(?:announce|scrape)/([a-fA-F0-9]{20,64})|/([a-fA-F0-9]{20,64})/(?:announce|scrape)")


def sanitize_tracker_url(url: str) -> str:
    """
    Sanitize sensitive passkeys, credentials, and authentication tokens from tracker URLs.
    
    Examples:
        - http://tracker.com/announce?passkey=secret123 -> http://tracker.com/announce?passkey=[REDACTED]
        - http://user:pass@tracker.com/announce -> http://user:[REDACTED]@tracker.com/announce
        - http://tracker.com/announce/0123456789abcdef0123456789abcdef -> http://tracker.com/announce/[REDACTED]
    """
    if not url or not isinstance(url, str):
        return ""

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return "[INVALID_URL]"

    # 1. Redact HTTP basic auth password
    netloc = parsed.netloc
    if "@" in netloc:
        user_info, host_port = netloc.split("@", 1)
        if ":" in user_info:
            username, _ = user_info.split(":", 1)
            netloc = f"{username}:[REDACTED]@{host_port}"
        else:
            netloc = f"[REDACTED]@{host_port}"

    # 2. Redact token in path if matching private tracker path patterns
    path = parsed.path
    match = PATH_TOKEN_PATTERN.search(path)
    if match:
        token = match.group(1) or match.group(2)
        if token:
            path = path.replace(token, "[REDACTED]")

    # 3. Redact query parameters
    query = parsed.query
    if query:
        query_params = urllib.parse.parse_qsl(query, keep_blank_values=True)
        sanitized_params = []
        for key, value in query_params:
            if key.lower() in SENSITIVE_PARAM_NAMES or len(value) >= 32 and re.match(r"^[a-fA-F0-9]{32,64}$", value):
                sanitized_params.append((key, "[REDACTED]"))
            else:
                sanitized_params.append((key, value))
        query = urllib.parse.urlencode(sanitized_params, safe="[]")

    sanitized = urllib.parse.urlunsplit((parsed.scheme, netloc, path, query, parsed.fragment))
    return sanitized


def sanitize_tracker_urls(urls: List[str]) -> List[str]:
    """Sanitize a list of tracker URLs, eliminating duplicates while preserving order."""
    seen = set()
    sanitized_list = []
    for u in urls:
        clean = sanitize_tracker_url(u.strip())
        if clean and clean not in seen:
            seen.add(clean)
            sanitized_list.append(clean)
    return sanitized_list


def is_private_tracker_url(url: str) -> bool:
    """
    Check if a tracker URL contains private user credentials, passkeys, or personal tokens.
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False

    # 1. Contains userinfo (username:password)
    if "@" in parsed.netloc:
        return True

    # 2. Path contains private hex/base32 token
    if PATH_TOKEN_PATTERN.search(parsed.path):
        return True

    # 3. Query contains passkey/auth parameters
    if parsed.query:
        query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        for key, value in query_params:
            if key.lower() in SENSITIVE_PARAM_NAMES:
                return True
            if len(value) >= 32 and re.match(r"^[a-fA-F0-9]{32,64}$", value):
                return True

    return False


def sanitize_error_message(msg: str) -> str:
    """
    Scrub passkeys, tokens, and raw tracker URLs from exception/error messages.
    """
    if not msg:
        return ""

    # Replace URLs found in message with sanitized versions
    url_pattern = re.compile(r"https?://[^\s\"'>]+")

    def _replace_url(match):
        return sanitize_tracker_url(match.group(0))

    cleaned = url_pattern.sub(_replace_url, str(msg))

    # Also scrub standalone passkey patterns if any leaked
    for param in SENSITIVE_PARAM_NAMES:
        cleaned = re.sub(rf"({param}=)[^&\s]+", r"\1[REDACTED]", cleaned, flags=re.IGNORECASE)

    return cleaned
