import subprocess
import json
from pathlib import Path

target = Path("temp/chinese_long_video/street_angel_1937.mp4")
print("=== FFPROBE INSPECTION ===")
cmd = [
    "ffprobe", "-v", "quiet", "-print_format", "json",
    "-show_format", "-show_streams", str(target)
]
res = subprocess.run(cmd, capture_output=True, text=True)
data = json.loads(res.stdout)
fmt = data.get("format", {})
duration_sec = float(fmt.get("duration", 0))
size_bytes = int(fmt.get("size", 0))
bitrate_kbps = int(fmt.get("bit_rate", 0)) / 1000

print(f"Filename: {target.name}")
print(f"Size: {size_bytes} bytes ({size_bytes / (1024*1024):.2f} MB)")
print(f"Duration: {duration_sec:.2f}s ({duration_sec / 60:.2f} min)")
print(f"Bitrate: {bitrate_kbps:.1f} kbps")

for i, s in enumerate(data.get("streams", [])):
    codec_type = s.get("codec_type")
    codec_name = s.get("codec_name")
    if codec_type == "video":
        w = s.get("width")
        h = s.get("height")
        fps = s.get("r_frame_rate")
        nb_frames = s.get("nb_frames")
        print(f"Stream {i} [VIDEO]: codec={codec_name}, res={w}x{h}, fps={fps}, frames={nb_frames}")
    elif codec_type == "audio":
        sr = s.get("sample_rate")
        ch = s.get("channels")
        ch_layout = s.get("channel_layout")
        print(f"Stream {i} [AUDIO]: codec={codec_name}, sample_rate={sr}Hz, channels={ch} ({ch_layout})")

print("\n=== MULTI-OFFSET DECODE VERIFICATION ===")
offsets = [0, 1800, 3600, 5000]
for offset in offsets:
    decode_cmd = [
        "ffmpeg", "-v", "error", "-ss", str(offset),
        "-i", str(target), "-t", "15",
        "-f", "null", "-"
    ]
    decode_res = subprocess.run(decode_cmd, capture_output=True, text=True)
    err = decode_res.stderr.strip()
    status = "PASS (0 errors)" if not err else f"ERROR: {err}"
    print(f"Offset {offset:>4}s (15s sample): {status}")

print("\n=== FULL DECODE TEST ===")
print("Running complete decode across entire 90m duration...")
import time
t0 = time.monotonic()
full_cmd = ["ffmpeg", "-v", "error", "-i", str(target), "-f", "null", "-"]
full_res = subprocess.run(full_cmd, capture_output=True, text=True)
t1 = time.monotonic()
full_err = full_res.stderr.strip()
full_status = "FULL DECODE PASS (0 errors)" if not full_err else f"DECODE ERRORS: {full_err}"
print(f"{full_status} in {t1 - t0:.2f}s")
