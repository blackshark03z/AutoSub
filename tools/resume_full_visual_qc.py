from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.simple_workflow import resume_interrupted_full_visual_qc


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume an interrupted Full Visual run from its rendered candidate QC checkpoint.")
    parser.add_argument("run_id")
    args = parser.parse_args()
    result = resume_interrupted_full_visual_qc(args.run_id)
    summary = {
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "phase": result.get("phase"),
        "output_path": result.get("output_path"),
        "output_hash": result.get("output_hash"),
        "recovered_from_visual_qc_checkpoint": result.get("recovered_from_visual_qc_checkpoint", False),
    }
    print(json.dumps(summary, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
