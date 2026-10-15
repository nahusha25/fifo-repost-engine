"""Tests for the static demo HTML page generator."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root so examples can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_generate_demo_html(tmp_path: Path) -> None:
    """Generate the demo page to a temp path and assert key content."""
    from examples.generate_demo import generate_demo_html

    out_file = tmp_path / "demo.html"
    content = generate_demo_html(out_file)

    assert out_file.exists()
    assert "6800" in content
    assert "680" in content
    assert "4000" in content
    key_insight = (
        "Under FIFO the 15 Jan Delivery Note value does not change (4,000). "
        "The Manufacture entry and the 25 Jan Milk Powder Delivery Note do. "
        "COGS +1,000."
    )
    assert key_insight in content
