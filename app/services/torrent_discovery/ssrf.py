"""
AutoSub Cloud V2 — SSRF Defense for BitTorrent Tracker Probes & Redirects
Guards against private IP probing, loopback, link-local, carrier-grade NAT,
cloud metadata endpoints (AWS/GCP/Azure/Alibaba), and SSRF via HTTP redirects.
"""

import ipaddress
import socket
import urllib.parse
import urllib.request
from typing import List, Optional, Set, Tuple, Union


class SSRFBlockedError(Exception):
    """Raised when a tracker URL or redirect target violates SSRF security boundaries."""
    pass


# Prohibited hostnames explicitly used by cloud metadata services
PROHIBITED_HOSTNAMES: Set[str] = {
    "localhost",
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
    cloud metadata, or carrier-grade NAT ranges.
    """
    ip_str = str(ip)

    # 1. Cloud metadata exact matches
    if ip_str in CLOUD_METADATA_IPS:
        return True, f"Prohibited cloud metadata IP: {ip_str}"

    # 2. Unspecified (0.0.0.0 / ::)
    if ip.is_unspecified:
        return True, f"Unspecified (0.0.0.0 / ::) address prohibited: {ip_str}"

    # 3. Loopback addresses (127.0.0.0/8, ::1)
    if ip.is_loopback:
        return True, f"Loopback address prohibited: {ip_str}"

    # 4. Private network ranges (RFC 1918: 10/8, 172.16/12, 192.168/16; RFC 4193: fc00::/7)
    if ip.is_private:
        return True, f"Private RFC 1918/4193 address prohibited: {ip_str}"

    # 4. Link-local (RFC 3927: 169.254.0.0/16, fe80::/10)
    if ip.is_link_local:
        return True, f"Link-local address prohibited: {ip_str}"

    # 5. Multicast and Reserved
    if ip.is_multicast:
        return True, f"Multicast address prohibited: {ip_str}"
    if ip.is_reserved:
        return True, f"Reserved address prohibited: {ip_str}"
    if ip.is_unspecified:
        return True, f"Unspecified (0.0.0.0 / ::) address prohibited: {ip_str}"

    # 6. Carrier-Grade NAT (RFC 6598: 100.64.0.0/10)
    if isinstance(ip, ipaddress.IPv4Address):
        cgnat_network = ipaddress.IPv4Network("100.64.0.0/10")
        if ip in cgnat_network:
            return True, f"Carrier-Grade NAT address prohibited: {ip_str}"

    # 7. IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return is_prohibited_ip(ip.ipv4_mapped)

    return False, ""


def resolve_and_validate_hostname(hostname: str) -> List[str]:
    """
    Resolve hostname to IP addresses via DNS and ensure none of the resolved IPs
    are private, loopback, or cloud metadata addresses.
    
    Returns:
        List of validated safe IP address strings.
    Raises:
        SSRFBlockedError: If hostname or any resolved IP is prohibited.
    """
    host_clean = hostname.strip().lower()

    if host_clean in PROHIBITED_HOSTNAMES:
        raise SSRFBlockedError(f"Prohibited hostname: {host_clean}")

    # Check if the hostname is a direct IP literal
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


def validate_tracker_url(url: str) -> None:
    """
    Validate tracker URL against SSRF rules:
    - Scheme must be http or https
    - Hostname must not resolve to prohibited/internal addresses
    - Port must not target dangerous services
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception as exc:
        raise SSRFBlockedError(f"Malformed URL: {exc}") from exc

    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFBlockedError(f"Unsupported tracker URL scheme '{parsed.scheme}'. Only HTTP/HTTPS allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError(f"Missing hostname in tracker URL: {url}")

    port = parsed.port
    if port is not None and port in DANGEROUS_PORTS:
        raise SSRFBlockedError(f"Port {port} in tracker URL is blocked for security reasons.")

    resolve_and_validate_hostname(hostname)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Custom HTTP redirect handler that validates target Location on every redirect
    to prevent SSRF via open redirects to internal/metadata services.
    """

    def __init__(self, max_redirects: int = 3):
        super().__init__()
        self.max_redirects = max_redirects
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise SSRFBlockedError(f"Too many redirects (exceeded limit of {self.max_redirects})")

        # Validate the redirect target URL against SSRF boundaries
        validate_tracker_url(newurl)

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def create_safe_tracker_opener(timeout_seconds: float = 5.0) -> urllib.request.OpenerDirector:
    """
    Create a safe urllib opener equipped with SafeRedirectHandler and SSRF validation.
    """
    opener = urllib.request.build_opener(SafeRedirectHandler(max_redirects=3))
    return opener
