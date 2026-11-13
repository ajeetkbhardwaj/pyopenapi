from __future__ import annotations

import sys
from pathlib import Path

ROUTER_SRC = Path(__file__).resolve().parents[1] / "src"
if str(ROUTER_SRC) not in sys.path:
    sys.path.insert(0, str(ROUTER_SRC))
