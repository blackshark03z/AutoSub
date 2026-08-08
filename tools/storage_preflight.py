from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.preflight import storage_preflight


def main() -> None:
    parser = argparse.ArgumentParser(description="Run tiered storage preflight.")
    parser.add_argument("--operation", required=True, choices=["run", "media", "package"])
    parser.add_argument("--path", default=None)
    parser.add_argument("--projected-workspace-bytes", type=int, default=None)
    parser.add_argument("--safety-reserve-bytes", type=int, default=1024**3)
    args = parser.parse_args()

    result = storage_preflight(
        args.operation,
        Path(args.path) if args.path else None,
        projected_workspace_bytes=args.projected_workspace_bytes,
        safety_reserve_bytes=args.safety_reserve_bytes,
    )
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
