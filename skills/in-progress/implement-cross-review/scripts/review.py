#!/usr/bin/env python3
"""Resolve repo resources through a locally linked skill directory."""
from pathlib import Path
import runpy
import sys

scripts = Path(__file__).resolve().parents[4] / "scripts"
entry = scripts / "cross-review.py"
if not entry.is_file():
    sys.exit("Cross-review requires the complete fork checkout; a copied skill folder is insufficient.")
sys.path.insert(0, str(scripts))
runpy.run_path(str(entry), run_name="__main__")
