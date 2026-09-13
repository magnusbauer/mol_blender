"""Run one D4 probe after installing the repository boundary audit hook."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import sys

import c1_boundary_guard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", required=True, type=Path)
    parser.add_argument("script", type=Path)
    parser.add_argument("script_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    c1_boundary_guard.attempts.clear()
    previous_argv = sys.argv
    sys.argv = [str(args.script), *args.script_args]
    try:
        runpy.run_path(str(args.script.resolve()), run_name="__main__")
    finally:
        sys.argv = previous_argv
        args.attempts.write_text(json.dumps({"attempts": c1_boundary_guard.attempts}) + "\n")
    if c1_boundary_guard.attempts:
        raise RuntimeError("D4 probe attempted a prohibited process or network boundary")


if __name__ == "__main__":
    main()
