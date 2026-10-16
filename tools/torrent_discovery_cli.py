#!/usr/bin/env python3
"""
Operational CLI runner for Torrent Source Discovery.
Usage:
    python tools/torrent_discovery_cli.py "magnet:?xt=urn:btih:..." --pretty
    python tools/torrent_discovery_cli.py path/to/sample.torrent --pretty
"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.torrent_discovery.cli import main

if __name__ == "__main__":
    sys.exit(main())
