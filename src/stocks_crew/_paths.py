# _paths.py -- make packages/shared-* importable from a checkout.
#
# Labs 2-4 do this with a sys.path loop at the top of each service. Lab 1 does
# it once, here, and every module that needs a shared package imports this
# first. It is a no-op when the directories are already on the path.
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

for _name in ("shared-config", "shared-guardrails", "shared-observability", "shared-models", "shared-datasource"):
    _p = str(REPO_ROOT / "packages" / _name)
    if _p not in sys.path:
        sys.path.insert(0, _p)
