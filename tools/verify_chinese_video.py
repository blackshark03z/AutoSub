#!/usr/bin/env python3
"""AutoSub Evidence Verifier — Evidence Integrity & Decode Verification

Enforces strict verification standards:
1. FFprobe and FFmpeg exit codes MUST be 0.
2. Validates JSON structure, stream presence (audio + video), and valid duration.
3. Extracts ACTUAL decoded frame count from FFmpeg decoder stderr (no inference from metadata).
4. Strictly distinguishes verification levels:
   - FULL_DECODE_VERIFIED
   - SAMPLE_DECODE_VERIFIED
   - METADATA_ONLY
   - VERIFICATION_FAILED
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def verify_media_evidence(
    target_path: Path,
    mode: str = "full",
    sample_offsets: list[int] | None = None,
    sample_duration: int = 15,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "target_file": str(target_path),
        "exists": target_path.exists(),
        "verification_mode": mode,
        "verdict": "VERIFICATION_FAILED",
        "ffprobe": {},
        "decode_verification": {},
        "errors": [],
    }

    if not target_path.exists():
        result["errors"].append(f"Target file does not exist: {target_path}")
        return result

    # ---------------------------------------------------------------------------
    # 1. FFprobe Inspection (Must exit 0 with valid streams and duration)
    # ---------------------------------------------------------------------------
    probe_cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(target_path)
    ]
    probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True)
    if probe_proc.returncode != 0:
        result["errors"].append(f"FFprobe failed with exit code {probe_proc.returncode}: {probe_proc.stderr.strip()}")
        return result

    try:
        probe_data = json.loads(probe_proc.stdout)
    except json.JSONDecodeError as exc:
        result["errors"].append(f"FFprobe returned invalid JSON: {exc}")
        return result

    fmt = probe_data.get("format", {})
    streams = probe_data.get("streams", [])
    try:
        duration_sec = float(fmt.get("duration", 0))
    except (ValueError, TypeError):
        duration_sec = 0.0

    if duration_sec <= 0:
        result["errors"].append(f"Invalid duration detected in format header: {duration_sec}s")
        return result

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        result["errors"].append("No video stream found in media file")
        return result
    if not audio_streams:
        result["errors"].append("No audio stream found in media file")
        return result

    v_stream = video_streams[0]
    a_stream = audio_streams[0]

    result["ffprobe"] = {
        "duration_seconds": duration_sec,
        "duration_minutes": round(duration_sec / 60.0, 2),
        "size_bytes": int(fmt.get("size", 0)),
        "bitrate_kbps": round(int(fmt.get("bit_rate", 0)) / 1000.0, 1),
        "format_name": fmt.get("format_name"),
        "video": {
            "codec": v_stream.get("codec_name"),
            "resolution": f"{v_stream.get('width')}x{v_stream.get('height')}",
            "fps": v_stream.get("r_frame_rate"),
            "header_nb_frames": v_stream.get("nb_frames"),
        },
        "audio": {
            "codec": a_stream.get("codec_name"),
            "sample_rate": a_stream.get("sample_rate"),
            "channels": a_stream.get("channels"),
            "channel_layout": a_stream.get("channel_layout"),
        },
    }

    if mode == "metadata_only":
        result["verdict"] = "METADATA_ONLY"
        return result

    # ---------------------------------------------------------------------------
    # 2. Multi-Offset Sample Decode Verification
    # ---------------------------------------------------------------------------
    offsets = sample_offsets if sample_offsets is not None else [0, 1800, 3600, 5000]
    # Filter offsets that are within duration
    valid_offsets = [o for o in offsets if o < duration_sec]
    if not valid_offsets:
        valid_offsets = [0]

    sample_results = []
    all_samples_passed = True

    for offset in valid_offsets:
        sample_cmd = [
            "ffmpeg", "-v", "error", "-ss", str(offset),
            "-i", str(target_path), "-t", str(sample_duration),
            "-f", "null", "-"
        ]
        s_proc = subprocess.run(sample_cmd, capture_output=True, text=True)
        s_err = s_proc.stderr.strip()
        passed = (s_proc.returncode == 0) and (not s_err)
        if not passed:
            all_samples_passed = False
        sample_results.append({
            "offset_seconds": offset,
            "sample_duration": sample_duration,
            "exit_code": s_proc.returncode,
            "status": "PASS (0 errors)" if passed else f"FAILED: {s_err or f'exit {s_proc.returncode}'}",
        })

    result["decode_verification"]["sample_tests"] = sample_results

    if not all_samples_passed:
        result["errors"].append("One or more sample decode offsets encountered errors")
        result["verdict"] = "DECODE_FAILED"
        return result

    if mode == "sample":
        result["verdict"] = "SAMPLE_DECODE_VERIFIED"
        return result

    # ---------------------------------------------------------------------------
    # 3. Full-Length 100% Decode Verification (Actual Decoded Frame Count Extraction)
    # ---------------------------------------------------------------------------
    t0 = time.monotonic()
    # Using -v info -stats enables FFmpeg to report progress and total decoded frames on stderr
    full_cmd = ["ffmpeg", "-v", "info", "-stats", "-i", str(target_path), "-f", "null", "-"]
    full_proc = subprocess.run(full_cmd, capture_output=True, text=True)
    elapsed = round(time.monotonic() - t0, 2)

    if full_proc.returncode != 0:
        result["errors"].append(f"Full decode failed with exit code {full_proc.returncode}: {full_proc.stderr[-500:]}")
        result["verdict"] = "DECODE_FAILED"
        return result

    # Extract actual decoded frames from FFmpeg stderr statistics
    frame_matches = re.findall(r"frame=\s*(\d+)", full_proc.stderr)
    actual_frames = int(frame_matches[-1]) if frame_matches else None

    # Check for decoding error strings in stderr
    error_lines = [
        line for line in full_proc.stderr.splitlines()
        if any(err_kw in line.lower() for err_kw in ["error", "corrupt", "invalid data", "crc mismatch"])
    ]

    result["decode_verification"]["full_test"] = {
        "exit_code": full_proc.returncode,
        "elapsed_seconds": elapsed,
        "actual_frames_decoded": actual_frames,
        "decoder_error_lines": error_lines,
        "verdict": "FULL_DECODE_PASS" if (not error_lines and actual_frames and actual_frames > 0) else "DECODE_WARNING",
    }

    if error_lines:
        result["errors"].append(f"Decoder logged {len(error_lines)} errors during full playback")
        result["verdict"] = "DECODE_FAILED"
    else:
        result["verdict"] = "FULL_DECODE_VERIFIED"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoSub Evidence Verifier")
    parser.add_argument("--path", type=str, default="temp/chinese_long_video/street_angel_1937.mp4", help="Path to video file")
    parser.add_argument("--mode", choices=["metadata_only", "sample", "full"], default="full", help="Verification mode")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    target = Path(args.path)
    res = verify_media_evidence(target, mode=args.mode)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("=== AUTOSUB EVIDENCE VERIFICATION REPORT ===")
        print(f"Target: {res['target_file']}")
        print(f"Verdict: {res['verdict']}")
        if res["ffprobe"]:
            ff = res["ffprobe"]
            print(f"Duration: {ff.get('duration_seconds')}s ({ff.get('duration_minutes')} min)")
            print(f"Video: {ff['video']['codec']} @ {ff['video']['resolution']} ({ff['video']['fps']} fps)")
            print(f"Audio: {ff['audio']['codec']} @ {ff['audio']['sample_rate']}Hz ({ff['audio']['channels']} ch)")
        if "sample_tests" in res["decode_verification"]:
            print("\nSample Offset Decodes:")
            for s in res["decode_verification"]["sample_tests"]:
                print(f"  Offset {s['offset_seconds']:>5}s: {s['status']}")
        if "full_test" in res["decode_verification"]:
            ft = res["decode_verification"]["full_test"]
            print(f"\nFull Decode: {ft['verdict']} in {ft['elapsed_seconds']}s")
            print(f"Actual Decoded Frames: {ft['actual_frames_decoded']}")
        if res["errors"]:
            print("\nErrors:")
            for err in res["errors"]:
                print(f"  - {err}")

    return 0 if res["verdict"] in {"FULL_DECODE_VERIFIED", "SAMPLE_DECODE_VERIFIED", "METADATA_ONLY"} else 1


if __name__ == "__main__":
    sys.exit(main())
