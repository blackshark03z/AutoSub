"""Repository-wide test-lane routing for retained release artifacts.

The Engineering contract keeps package and historical-artifact assertions in
the explicit ``release`` lane.  These markers alter collection only; the test
implementations and their assertions remain unchanged.
"""

from __future__ import annotations

import pytest


_RELEASE_MODULE_PREFIXES = (
    "tests/test_cp11c_ocr_addon_package.py::",
    "tests/test_cp11d_full_portable_package.py::",
)
_RELEASE_NODE_IDS = {
    "tests/test_cp12a_creative_subtitle_tracks.py::test_cp12a_cp11d_and_accepted_media_immutability",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Route retained package/hash checks to ``pytest -m release``."""
    for item in items:
        if item.nodeid.startswith(_RELEASE_MODULE_PREFIXES) or item.nodeid in _RELEASE_NODE_IDS:
            item.add_marker(pytest.mark.release)
