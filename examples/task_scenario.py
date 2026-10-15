"""Runnable demonstration of the Milk -> Milk Powder task scenario.

Thin wrapper delegating to stock_repost.cli.main().
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is importable when running standalone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_repost.cli import main

if __name__ == "__main__":
    sys.exit(main())
