#!/usr/bin/env python3
"""Use the shared handoff with a required parent spec for this workflow."""
from pathlib import Path
import runpy
import sys

scripts = Path(__file__).resolve().parents[4] / "scripts"
entry = scripts / "cross-review.py"
if not entry.is_file():
    sys.exit("Cross-review requires the complete fork checkout; a copied skill folder is insufficient.")
if len(sys.argv) > 1 and sys.argv[1] == "begin":
    sys.argv.append("--require-spec")
sys.path.insert(0, str(scripts))
runpy.run_path(str(entry), run_name="__main__")
