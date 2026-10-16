"""
AutoSub Cloud V2 — Torrent Discovery CLI Utility
"""

import argparse
import json
import sys

from app.services.torrent_discovery.models import DiscoveryMode, TorrentDiscoveryRequest
from app.services.torrent_discovery.service import default_torrent_discovery_service


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AutoSub Cloud V2 — Torrent Discovery CLI Tool"
    )
    parser.add_argument(
        "source",
        help="Magnet URI or path to .torrent file",
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "network_probe"],
        default="offline",
        help="Discovery mode (default: offline)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout in seconds (default: 10.0)",
    )
    parser.add_argument(
        "--allow-probe",
        action="store_true",
        help="Explicit operator authorization for network probing",
    )
    parser.add_argument(
        "--engine",
        choices=["builtin", "libtorrent"],
        default="builtin",
        help="Preferred discovery engine (default: builtin)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Format output with indentation",
    )

    args = parser.parse_args()

    request = TorrentDiscoveryRequest(
        source=args.source,
        mode=DiscoveryMode(args.mode),
        timeout_seconds=args.timeout,
        probe_allowed=args.allow_probe,
        preferred_engine=args.engine,
    )

    result = default_torrent_discovery_service.discover(request)
    output_dict = result.to_dict()

    if args.pretty:
        print(json.dumps(output_dict, indent=2))
    else:
        print(json.dumps(output_dict))

    return 0 if result.status in ("METADATA_AVAILABLE", "INFOHASH_ONLY", "METADATA_PENDING") else 1


if __name__ == "__main__":
    sys.exit(main())
