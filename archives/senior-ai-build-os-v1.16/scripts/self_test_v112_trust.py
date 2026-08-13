#!/usr/bin/env python3
"""Focused v1.12 trust-boundary regression: Goal revision budget and R3 external attestation."""
from __future__ import annotations
import tempfile
from pathlib import Path
from self_test_v112 import test_revision_budget_and_r3_attestation

def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ai-os-v112-trust-") as td:
        test_revision_budget_and_r3_attestation(Path(td))
    print("SELF_TEST_V112_TRUST: PASS")

if __name__ == "__main__":
    main()
