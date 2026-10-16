"""
AutoSub Cloud V2 — SSRF & DNS-Rebinding Hardening for BitTorrent Tracker Probes
Guards against private IP probing, loopback, link-local, carrier-grade NAT,
cloud metadata endpoints (AWS/GCP/Azure/Alibaba), DNS rebinding (TOCTOU),
and SSRF via HTTP redirect chains.
"""

import http.client
import ipaddress
import re
import socket
import ssl
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Set, Tuple, Union


class SSRFBlockedError(Exception):
    """Raised when a tracker URL or redirect target violates SSRF security boundaries."""
    pass


# Prohibited hostnames explicitly used by cloud metadata services or local host
PROHIBITED_HOSTNAMES: Set[str] = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "instance-data",
}

# Cloud metadata and special IP addresses that must be blocked
CLOUD_METADATA_IPS: Set[str] = {
    "169.254.169.254",   # AWS, GCP, Azure, OpenStack, DigitalOcean
    "169.254.169.253",   # AWS DNS
    "100.100.100.200",   # Alibaba Cloud metadata
    "fd00:ec2::254",     # AWS IPv6 metadata
}

# Dangerous service ports that should not be targeted by tracker scrapes
DANGEROUS_PORTS: Set[int] = {
    21,    # FTP
    22,    # SSH
    23,    # Telnet
    25,    # SMTP
    110,   # POP3
    111,   # rpcbind
    135,   # RPC
    137, 138, 139, 445,  # NetBIOS / SMB
    2049,  # NFS
    2375, 2376,  # Docker daemon
    3306,  # MySQL
    5432,  # PostgreSQL
    6379,  # Redis
    8500,  # Consul
    9200,  # Elasticsearch
    10250, # Kubelet
    11211, # Memcached
    27017, # MongoDB
}


def is_prohibited_ip(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> Tuple[bool, str]:
    """
    Check if an IP address belongs to loopback, private, link-local, reserved,
    cloud metadata, carrier-grade NAT, IPv4-mapped IPv6, 6to4, or Teredo ranges.
    """
    ip_str = str(ip)

    # 1. Cloud metadata exact matches
    if ip_str in CLOUD_METADATA_IPS:
        return True, f"Prohibited cloud metadata IP: {ip_str}"

    # 2. IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1, [::ffff:169.254.169.254])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return is_prohibited_ip(ip.ipv4_mapped)

    # 3. Unspecified (0.0.0.0 / ::)
    if ip.is_unspecified:
        return True, f"Unspecified (0.0.0.0 / ::) address prohibited: {ip_str}"

    # 4. Loopback addresses (127.0.0.0/8, ::1)
    if ip.is_loopback:
        return True, f"Loopback address prohibited: {ip_str}"

    # 5. Private network ranges (RFC 1918: 10/8, 172.16/12, 192.168/16; RFC 4193: fc00::/7)
    if ip.is_private:
        return True, f"Private RFC 1918/4193 address prohibited: {ip_str}"

    # 6. Link-local (RFC 3927: 169.254.0.0/16, fe80::/10)
    if ip.is_link_local:
        return True, f"Link-local address prohibited: {ip_str}"

    # 7. Multicast and Reserved
    if ip.is_multicast:
        return True, f"Multicast address prohibited: {ip_str}"
    if ip.is_reserved:
        return True, f"Reserved address prohibited: {ip_str}"

    # 8. Carrier-Grade NAT (RFC 6598: 100.64.0.0/10)
    if isinstance(ip, ipaddress.IPv4Address):
        cgnat_network = ipaddress.IPv4Network("100.64.0.0/10")
        if ip in cgnat_network:
            return True, f"Carrier-Grade NAT address prohibited: {ip_str}"

    # 9. 6to4 addresses (2002::/16) embedding private IPv4
    if isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.IPv6Network("2002::/16"):
        embedded_ipv4 = ipaddress.IPv4Address(ip.packed[2:6])
        is_bad, reason = is_prohibited_ip(embedded_ipv4)
        if is_bad:
            return True, f"6to4 embedded prohibited IP: {reason}"

    # 10. Teredo addresses (2001:0::/32) embedding client IPv4 (inverted)
    if isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.IPv6Network("2001:0::/32"):
        embedded_bytes = bytes(b ^ 0xFF for b in ip.packed[12:16])
        embedded_ipv4 = ipaddress.IPv4Address(embedded_bytes)
        is_bad, reason = is_prohibited_ip(embedded_ipv4)
        if is_bad:
            return True, f"Teredo embedded prohibited IP: {reason}"

    return False, ""


def resolve_and_validate_hostname(hostname: str) -> List[str]:
    """
    Resolve hostname to IP addresses via DNS and ensure none of the resolved IPs
    are private, loopback, or cloud metadata addresses.
    
    Handles:
        - Direct IP literals (IPv4 and IPv6)
        - Decimal integer IP literals (e.g. 2130706433 -> 127.0.0.1)
        - Hexadecimal IP literals (e.g. 0x7f000001 -> 127.0.0.1)
        - Hostname DNS pre-validation
        
    Returns:
        List of validated safe IP address strings.
    Raises:
        SSRFBlockedError: If hostname or any resolved IP is prohibited.
    """
    host_clean = hostname.strip().lower()

    if host_clean in PROHIBITED_HOSTNAMES:
        raise SSRFBlockedError(f"Prohibited hostname: {host_clean}")

    # Check decimal integer IP representation (e.g., 2130706433 -> 127.0.0.1)
    if host_clean.isdigit():
        try:
            num = int(host_clean)
            if 0 <= num <= 0xFFFFFFFF:
                ip = ipaddress.IPv4Address(num)
                is_bad, reason = is_prohibited_ip(ip)
                if is_bad:
                    raise SSRFBlockedError(f"Decimal IP '{host_clean}' ({ip}) prohibited: {reason}")
                return [str(ip)]
        except ValueError:
            pass

    # Check hex integer IP representation (e.g., 0x7f000001)
    if host_clean.startswith("0x") and not any(c in host_clean for c in ":."):
        try:
            num = int(host_clean, 16)
            if 0 <= num <= 0xFFFFFFFF:
                ip = ipaddress.IPv4Address(num)
                is_bad, reason = is_prohibited_ip(ip)
                if is_bad:
                    raise SSRFBlockedError(f"Hex IP '{host_clean}' ({ip}) prohibited: {reason}")
                return [str(ip)]
        except ValueError:
            pass

    # Direct IP literal check
    try:
        ip = ipaddress.ip_address(host_clean)
    except ValueError:
        ip = None

    if ip is not None:
        is_bad, reason = is_prohibited_ip(ip)
        if is_bad:
            raise SSRFBlockedError(reason)
        return [str(ip)]

    # Domain name, proceed to DNS resolution
    try:
        addr_info = socket.getaddrinfo(host_clean, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"DNS resolution failed for hostname '{host_clean}': {exc}") from exc

    resolved_ips: List[str] = []
    for entry in addr_info:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise SSRFBlockedError(f"Invalid resolved IP '{ip_str}' for host '{host_clean}'") from exc

        is_bad, reason = is_prohibited_ip(ip)
        if is_bad:
            raise SSRFBlockedError(f"DNS for '{host_clean}' resolved to prohibited IP: {reason}")
        resolved_ips.append(ip_str)

    if not resolved_ips:
        raise SSRFBlockedError(f"No valid IP addresses resolved for hostname '{host_clean}'")

    return resolved_ips


def validate_tracker_url(url: str) -> str:
    """
    Validate tracker URL against SSRF rules:
    - Scheme must be strictly http or https
    - Hostname must not resolve to prohibited/internal addresses
    - Port must not target dangerous services
    - Userinfo (user:pass@) in URL is forbidden in tracker scrape URLs
    
    Returns:
        The validated pinned IP address (str) to be used for DNS-rebinding-safe connection.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception as exc:
        raise SSRFBlockedError(f"Malformed URL: {exc}") from exc

    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFBlockedError(f"Unsupported tracker URL scheme '{parsed.scheme}'. Only HTTP/HTTPS allowed.")

    if "@" in parsed.netloc:
        raise SSRFBlockedError("Userinfo credentials embedded in tracker URL are prohibited.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError(f"Missing hostname in tracker URL: {url}")

    port = parsed.port
    if port is not None:
        if not (1 <= port <= 65535):
            raise SSRFBlockedError(f"Invalid port {port} in tracker URL.")
        if port in DANGEROUS_PORTS:
            raise SSRFBlockedError(f"Port {port} in tracker URL is blocked for security reasons.")

    resolved_ips = resolve_and_validate_hostname(hostname)
    return resolved_ips[0]


# ============================================================================
# DNS Rebinding & Pinned IP Socket Transport
# ============================================================================

class PinnedIPHTTPConnection(http.client.HTTPConnection):
    """
    HTTP connection that connects directly to a pre-validated IP address,
    preventing DNS rebinding (TOCTOU) attacks while preserving the HTTP Host header.
    """

    def __init__(self, host, port=None, pinned_ip=None, **kwargs):
        super().__init__(host, port, **kwargs)
        self.pinned_ip = pinned_ip

    def connect(self):
        target_ip = self.pinned_ip or self.host
        # Connect directly to the pre-validated IP, avoiding second DNS resolution
        self.sock = socket.create_connection(
            (target_ip, self.port), self.timeout, self.source_address
        )


class PinnedIPHTTPSConnection(http.client.HTTPSConnection):
    """
    HTTPS connection that connects directly to a pre-validated IP address,
    preventing DNS rebinding while preserving SNI and TLS certificate verification.
    """

    def __init__(self, host, port=None, pinned_ip=None, **kwargs):
        super().__init__(host, port, **kwargs)
        self.pinned_ip = pinned_ip

    def connect(self):
        target_ip = self.pinned_ip or self.host
        sock = socket.create_connection(
            (target_ip, self.port), self.timeout, self.source_address
        )
        server_hostname = self.host.split(":")[0]
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


class PinnedIPHTTPHandler(urllib.request.HTTPHandler):
    """urllib HTTPHandler using DNS-rebinding-safe PinnedIPHTTPConnection."""

    def __init__(self, pinned_ip_map: Dict[str, str]):
        super().__init__()
        self.pinned_ip_map = pinned_ip_map

    def http_open(self, req):
        return self.do_open(self._get_connection, req)

    def _get_connection(self, host, **kwargs):
        hostname = host.split(":")[0].lower()
        pinned_ip = self.pinned_ip_map.get(hostname)
        return PinnedIPHTTPConnection(host, pinned_ip=pinned_ip, **kwargs)


class PinnedIPHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib HTTPSHandler using DNS-rebinding-safe PinnedIPHTTPSConnection."""

    def __init__(self, pinned_ip_map: Dict[str, str]):
        super().__init__()
        self.pinned_ip_map = pinned_ip_map

    def https_open(self, req):
        return self.do_open(self._get_connection, req)

    def _get_connection(self, host, **kwargs):
        hostname = host.split(":")[0].lower()
        pinned_ip = self.pinned_ip_map.get(hostname)
        return PinnedIPHTTPSConnection(host, pinned_ip=pinned_ip, **kwargs)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Custom HTTP redirect handler that:
    1. Intercepts 301, 302, 303, 307, 308 redirects.
    2. Validates destination against SSRF and cloud metadata boundaries.
    3. Pins the validated IP of the redirect target to prevent DNS rebinding.
    4. Limits redirect chains to max_redirects (default 3).
    """

    def __init__(self, max_redirects: int = 3, pinned_ip_map: Optional[Dict[str, str]] = None):
        super().__init__()
        self.max_redirects = max_redirects
        self.redirect_count = 0
        self.pinned_ip_map = pinned_ip_map if pinned_ip_map is not None else {}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise SSRFBlockedError(f"Too many redirects (exceeded limit of {self.max_redirects})")

        # Validate the redirect target URL and extract pinned IP
        validated_ip = validate_tracker_url(newurl)
        target_host = urllib.parse.urlsplit(newurl).hostname
        if target_host:
            self.pinned_ip_map[target_host.lower()] = validated_ip

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def create_safe_tracker_opener(
    initial_url: str, timeout_seconds: float = 5.0
) -> Tuple[urllib.request.OpenerDirector, str]:
    """
    Create a safe urllib OpenerDirector equipped with:
    - Pinned-IP transport (eliminates DNS rebinding TOCTOU)
    - SafeRedirectHandler (validates every redirect destination against SSRF)
    
    Returns:
        (opener, validated_ip)
    """
    validated_ip = validate_tracker_url(initial_url)
    hostname = urllib.parse.urlsplit(initial_url).hostname or ""
    pinned_map: Dict[str, str] = {hostname.lower(): validated_ip}

    redirect_handler = SafeRedirectHandler(max_redirects=3, pinned_ip_map=pinned_map)
    http_handler = PinnedIPHTTPHandler(pinned_ip_map=pinned_map)
    https_handler = PinnedIPHTTPSHandler(pinned_ip_map=pinned_map)

    opener = urllib.request.build_opener(redirect_handler, http_handler, https_handler)
    return opener, validated_ip
