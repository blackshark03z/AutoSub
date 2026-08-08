from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.hashing import sha256_file
from app.services.caption_analysis_runtime import load_caption_analysis_progress, run_caption_analysis_worker


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded embedded-caption analyzer without production DB or providers.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--progress-output", type=Path, required=True)
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument("--profile-output", type=Path)
    parser.add_argument("--ocr-config", type=Path)
    args = parser.parse_args()

    args.run_directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = run_caption_analysis_worker(
        args.source,
        args.run_directory,
        analysis_only=True,
        ocr_config_path=args.ocr_config,
    )
    duration = time.monotonic() - started
    progress = load_caption_analysis_progress(args.run_directory) or {}
    progress["diagnostic"] = {
        "source_sha256": sha256_file(args.source),
        "duration_seconds": round(duration, 3),
        "track_count": len(result.get("tracks") or []),
        "provider_calls": 0,
    }
    args.progress_output.parent.mkdir(parents=True, exist_ok=True)
    args.progress_output.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    analyzer_log = args.run_directory / "logs" / "embedded_caption_analyzer.log"
    args.log_output.parent.mkdir(parents=True, exist_ok=True)
    if analyzer_log.is_file():
        shutil.copy2(analyzer_log, args.log_output)
    else:
        args.log_output.write_text("Analyzer completed without log events.\n", encoding="utf-8")
    if args.profile_output:
        rows = list(progress.get("stage_history") or [])
        metrics = progress.get("supervisor_metrics") or {}
        with args.profile_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["stage", "started_at", "completed_at", "duration_seconds", "peak_memory_bytes", "average_cpu_percent"],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        **{key: row.get(key) for key in ("stage", "started_at", "completed_at", "duration_seconds")},
                        "peak_memory_bytes": metrics.get("peak_memory_bytes"),
                        "average_cpu_percent": metrics.get("average_cpu_percent"),
                    }
                )
    print(json.dumps(progress["diagnostic"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
