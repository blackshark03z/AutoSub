#!/usr/bin/env python3
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

bench_dir = Path("temp/downloader_bench")
bench_dir.mkdir(parents=True, exist_ok=True)

test_url = "https://archive.org/download/ElephantsDream/ed_1024_512kb.mp4"
results = {}

# 1. Direct Python HTTP Stream
print("Benchmarking Direct Python HTTP...")
t0 = time.monotonic()
dest = bench_dir / "direct.mp4"
downloaded = 0
req = urllib.request.Request(test_url, headers={"User-Agent": "AutoSub/2.0"})
with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
    while True:
        chunk = resp.read(1048576)
        if not chunk:
            break
        f.write(chunk)
        downloaded += len(chunk)
t1 = time.monotonic()
elapsed = t1 - t0
results["direct_http"] = {
    "engine": "Direct Python HTTP",
    "elapsed_s": round(elapsed, 2),
    "speed_mbs": round((downloaded / (1024 * 1024)) / elapsed, 2),
    "size_bytes": downloaded,
    "exit_code": 0,
    "resume_supported": True,
    "retry_supported": True,
}
print("Direct HTTP completed:", results["direct_http"])

# 2. yt-dlp
print("Benchmarking yt-dlp...")
dest_ytdlp = bench_dir / "ytdlp.mp4"
if dest_ytdlp.exists():
    dest_ytdlp.unlink()
t0 = time.monotonic()
p = subprocess.run(["yt-dlp", "-o", str(dest_ytdlp), test_url, "--no-part", "--quiet"], capture_output=True, text=True)
t1 = time.monotonic()
elapsed = t1 - t0
size = dest_ytdlp.stat().st_size if dest_ytdlp.exists() else 0
results["yt_dlp"] = {
    "engine": "yt-dlp",
    "elapsed_s": round(elapsed, 2),
    "speed_mbs": round((size / (1024 * 1024)) / elapsed, 2) if p.returncode == 0 else 0,
    "size_bytes": size,
    "exit_code": p.returncode,
    "resume_supported": True,
    "retry_supported": True,
}
print("yt-dlp completed:", results["yt_dlp"])

# 3. you-get
print("Benchmarking you-get...")
dest_youget = bench_dir / "youget.mp4"
if dest_youget.exists():
    dest_youget.unlink()
t0 = time.monotonic()
p = subprocess.run(["you-get", "-o", str(bench_dir), "-O", "youget.mp4", test_url], capture_output=True, text=True)
t1 = time.monotonic()
elapsed = t1 - t0
size = dest_youget.stat().st_size if dest_youget.exists() else 0
results["you_get"] = {
    "engine": "you-get",
    "elapsed_s": round(elapsed, 2),
    "speed_mbs": round((size / (1024 * 1024)) / elapsed, 2) if dest_youget.exists() else 0,
    "size_bytes": size,
    "exit_code": p.returncode,
    "resume_supported": False,
    "retry_supported": False,
}
print("you-get completed:", results["you_get"])

print("\n=== FINAL COMPARATIVE BENCHMARK ===")
print(json.dumps(results, indent=2))
