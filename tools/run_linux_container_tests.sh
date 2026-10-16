#!/usr/bin/env bash
set -euo pipefail

# AutoSub Cloud V2 — Linux CPU Container Test Runner (WP-DL-001B)
IMAGE_NAME="autosub-torrent-discovery:cpu"

echo "[1/2] Building Linux CPU container image..."
docker build -t "${IMAGE_NAME}" -f Dockerfile.cpu .

echo "[2/2] Running 63 torrent discovery tests inside Linux CPU container..."
docker run --rm "${IMAGE_NAME}"
