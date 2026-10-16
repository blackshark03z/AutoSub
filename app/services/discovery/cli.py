from __future__ import annotations

import argparse
import json
import sys

from app.services.discovery.worker import DiscoveryWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AutoSub Cloud V2 Video Source Discovery Worker (Metadata-Only Probe)"
    )
    parser.add_argument("url", nargs="?", help="Target video or stream manifest URL")
    parser.add_argument("--url", dest="opt_url", help="Target video or stream manifest URL")
    parser.add_argument("--platform", help="Target platform hint (e.g. bilibili, iqiyi, youku, manifest_hls)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Maximum probe timeout in seconds (default: 15.0)",
    )
    parser.add_argument(
        "--confirm-authorized",
        action="store_true",
        help="Confirm that the operator has legitimate authorization to probe this source",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Format JSON output with indentation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target_url = args.url or args.opt_url
    if not target_url:
        print(
            json.dumps({"error": "Missing required URL argument. Pass URL as positional or with --url."}, indent=2),
            file=sys.stderr,
        )
        return 1

    worker = DiscoveryWorker()
    result = worker.probe_url(
        video_url=target_url,
        platform=args.platform,
        timeout_seconds=args.timeout,
        authorized_access_confirmed=args.confirm_authorized,
    )

    indent = 2 if args.pretty else None
    print(json.dumps(result.to_dict(), indent=indent, ensure_ascii=False))

    # Exit code: 0 for DISCOVERABLE, 2 for DRM/AUTH/GEO, 1 for errors
    if result.status == "DISCOVERABLE":
        return 0
    elif result.status in {"DRM_SUSPECTED", "AUTH_REQUIRED", "GEO_RESTRICTED"}:
        return 0  # Still a successful probe finding
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
