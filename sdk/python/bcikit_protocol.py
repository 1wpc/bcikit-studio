"""Compatibility shim for existing BCIKit protocol scripts.

The canonical implementation now lives in :mod:`bcikit.protocol`. This path is
kept so documented ``PYTHONPATH=sdk/python`` commands still work in a checkout.
"""

import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from bcikit.protocol import *  # noqa: E402,F403

del Path, sys
