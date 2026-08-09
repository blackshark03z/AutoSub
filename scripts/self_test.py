#!/usr/bin/env python3
"""Default fast/core regression entrypoint (<30s target).

Focused older trust matrices remain separate CI jobs; the historical broad matrix
is self_test_full.py for release/nightly runs.
"""
from __future__ import annotations
import subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SUITES=[('fast','scripts/self_test_fast.py',30),('current','scripts/self_test_v116.py',60)]

def main()->None:
    for name,script,timeout in SUITES:
        print(f'== {name} ==',flush=True)
        try:r=subprocess.run([sys.executable,script],cwd=ROOT,text=True,timeout=timeout)
        except subprocess.TimeoutExpired as exc: raise SystemExit(f'SELF_TEST: FAIL {name} timeout={timeout}s') from exc
        if r.returncode: raise SystemExit(f'SELF_TEST: FAIL {name} exit={r.returncode}')
    print('SELF_TEST: PASS')
if __name__=='__main__':main()
