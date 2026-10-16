from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Tuple

CLOUD_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
    "169.254.169.254",
    "169.254.170.2",
    "100.100.100.200",
}

BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # IPv4 loopback
    ipaddress.ip_network("10.0.0.0/8"),       # IPv4 private class A
    ipaddress.ip_network("172.16.0.0/12"),    # IPv4 private class B
    ipaddress.ip_network("192.168.0.0/16"),   # IPv4 private class C
    ipaddress.ip_network("169.254.0.0/16"),   # IPv4 link-local & cloud metadata
    ipaddress.ip_network("100.64.0.0/10"),    # Carrier-grade NAT
    ipaddress.ip_network("0.0.0.0/8"),        # Current network
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
    ipaddress.ip_network("::/128"),           # IPv6 unspecified
]


class SsrfProtectionError(ValueError):
    """Raised when an external request targets a restricted network destination."""


def is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # 1. Unpack IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1, ::ffff:7f00:1, ::ffff:169.254.169.254)
    if isinstance(ip, ipaddress.IPv6Address) and getattr(ip, "ipv4_mapped", None) is not None:
        return is_ip_blocked(ip.ipv4_mapped)

    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return True
    for net in BLOCKED_IP_NETWORKS:
        if ip in net:
            return True
    # Explicit Alibaba Cloud metadata IP
    if str(ip) == "100.100.100.200":
        return True
    return False


def validate_proxy_url(proxy_url: str, resolve_dns: bool = True) -> Tuple[bool, str | None]:
    """Validate that a proxy URL does not target a loopback, private, or metadata endpoint."""
    if not proxy_url or not isinstance(proxy_url, str):
        return False, "EMPTY_OR_INVALID_PROXY_URL"
    return validate_url_ssrf(proxy_url, resolve_dns=resolve_dns)


def validate_proxies_dict(proxies: dict[str, str] | None, resolve_dns: bool = True) -> Tuple[bool, str | None]:
    """Validate all configured proxy endpoints against SSRF rules."""
    if not proxies:
        return True, None
    for proto, p_url in proxies.items():
        if not p_url:
            continue
        safe, reason = validate_proxy_url(p_url, resolve_dns=resolve_dns)
        if not safe:
            return False, f"Unsafe proxy for {proto} ({p_url}): {reason}"
    return True, None


class SafeHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that inspects the connected socket peer IP to defeat DNS rebinding."""

    def connect(self) -> None:
        super().connect()
        if self.sock:
            peer_ip_str = self.sock.getpeername()[0].strip("[]")
            try:
                peer_ip = ipaddress.ip_address(peer_ip_str)
            except ValueError:
                self.sock.close()
                raise SsrfProtectionError(f"SSRF violation: invalid peer IP {peer_ip_str}")

            if is_ip_blocked(peer_ip):
                self.sock.close()
                raise SsrfProtectionError(
                    f"SSRF violation: socket connected to blocked peer IP {peer_ip_str} (DNS rebinding detected)"
                )


class SafeHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that inspects the connected socket peer IP to defeat DNS rebinding."""

    def connect(self) -> None:
        super().connect()
        if self.sock:
            peer_ip_str = self.sock.getpeername()[0].strip("[]")
            try:
                peer_ip = ipaddress.ip_address(peer_ip_str)
            except ValueError:
                self.sock.close()
                raise SsrfProtectionError(f"SSRF violation: invalid peer IP {peer_ip_str}")

            if is_ip_blocked(peer_ip):
                self.sock.close()
                raise SsrfProtectionError(
                    f"SSRF violation: socket connected to blocked peer IP {peer_ip_str} (DNS rebinding detected)"
                )


class SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(SafeHTTPConnection, req)


class SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(
            SafeHTTPSConnection,
            req,
            context=getattr(self, "_context", None),
            check_hostname=getattr(self, "_check_hostname", None),
        )


def validate_url_ssrf(url: str, resolve_dns: bool = True) -> Tuple[bool, str | None]:
    """Validate that a URL is safe against SSRF attacks.
    
    Verifies URL scheme, hostname, and resolves IP addresses to block
    private, loopback, link-local, and cloud metadata targets.
    """
    if not url or not isinstance(url, str):
        return False, "EMPTY_OR_INVALID_URL"

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False, "MALFORMED_URL"

    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False, f"UNSUPPORTED_SCHEME: {scheme}"

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        return False, "MISSING_HOST"

    # Strip bracket notation for IPv6 literals (e.g. [::1] -> ::1)
    clean_host = hostname.strip("[]")

    if clean_host in {"localhost", "loopback"} or clean_host in CLOUD_METADATA_HOSTS:
        return False, f"BLOCKED_HOST: {clean_host}"

    # Check if clean_host is directly an IP literal
    try:
        ip = ipaddress.ip_address(clean_host)
        if is_ip_blocked(ip):
            return False, f"BLOCKED_IP_LITERAL: {ip}"
    except ValueError:
        # Hostname is a domain name, proceed to DNS resolution if requested
        if resolve_dns:
            try:
                addr_info = socket.getaddrinfo(clean_host, None)
                for entry in addr_info:
                    sockaddr = entry[4]
                    ip_str = sockaddr[0].strip("[]")
                    resolved_ip = ipaddress.ip_address(ip_str)
                    if is_ip_blocked(resolved_ip):
                        return False, f"BLOCKED_RESOLVED_IP: {resolved_ip}"
            except socket.gaierror as exc:
                return False, f"DNS_RESOLUTION_FAILED: {exc}"
            except Exception as exc:
                return False, f"DNS_CHECK_ERROR: {exc}"

    return True, None


def safe_http_fetch(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 10.0,
    max_bytes: int = 1048576,  # 1 MiB limit
    max_redirects: int = 5,
    proxies: dict[str, str] | None = None,
    resolve_dns: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    """Perform an SSRF-safe HTTP request with redirect inspection, byte caps, and proxy safety audit."""
    current_url = url
    redirect_count = 0
    req_headers = {"User-Agent": "AutoSub-Discovery/2.0 (Headless Video Probe)"}
    if headers:
        req_headers.update(headers)

    # Audit and lock down proxy handlers
    if proxies is not None:
        p_safe, p_reason = validate_proxies_dict(proxies, resolve_dns=resolve_dns)
        if not p_safe:
            raise SsrfProtectionError(f"Blocked unsafe proxy configuration: {p_reason}")
        proxy_handler = urllib.request.ProxyHandler(proxies)
    else:
        # Lock network mode: isolate from uninspected ambient environment proxies
        proxy_handler = urllib.request.ProxyHandler({})

    while True:
        safe, reason = validate_url_ssrf(current_url, resolve_dns=resolve_dns)
        if not safe:
            raise SsrfProtectionError(f"SSRF violation targeting {current_url}: {reason}")

        req = urllib.request.Request(current_url, headers=req_headers, method=method)
        # We handle redirects manually to enforce SSRF validation on every jump
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                return None

        handlers: list[Any] = [NoRedirectHandler, proxy_handler]
        if current_url.lower().startswith("https:"):
            try:
                import certifi
                import ssl
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                handlers.append(SafeHTTPSHandler(context=ssl_ctx))
            except Exception:
                handlers.append(SafeHTTPSHandler())
        else:
            handlers.append(SafeHTTPHandler())

        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(req, timeout=timeout_seconds) as response:
                status_code = response.status
                resp_headers = {k.lower(): v for k, v in response.headers.items()}
                # Read with strict length capping
                content = response.read(max_bytes + 1)
                if len(content) > max_bytes:
                    content = content[:max_bytes]
                return status_code, resp_headers, content
        except urllib.error.HTTPError as exc:
            # Check for redirect status codes (301, 302, 303, 307, 308)
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location")
                if not location:
                    raise SsrfProtectionError(f"HTTP {exc.code} redirect missing Location header")
                redirect_count += 1
                if redirect_count > max_redirects:
                    raise SsrfProtectionError(f"Excessive redirects (>{max_redirects})")
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            # For 4xx/5xx or non-redirects, return status code and body if available
            body = exc.read(max_bytes) if exc.fp else b""
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, body
        except (socket.timeout, TimeoutError) as exc:
            raise TimeoutError(f"Request to {current_url} timed out: {exc}")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise TimeoutError(f"Request to {current_url} timed out")
            raise ConnectionError(f"Network error accessing {current_url}: {exc.reason}")
