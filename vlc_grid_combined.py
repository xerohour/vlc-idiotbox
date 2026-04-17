#!/usr/bin/env python3
"""
Cross-platform launcher for the VLC grid apps in this folder.

Keeps the original files intact:
- `vlc_grid.py` for the Tk-based Windows-friendly launcher
- `vlc_grid (1).py` for the GTK/Adwaita launcher with Tk fallback
"""

from __future__ import annotations

import importlib.util
import os
import sys


BASE_DIR = os.path.dirname(__file__)


def _run_script(filename: str) -> None:
    path = os.path.join(BASE_DIR, filename)
    spec = importlib.util.spec_from_file_location(f"vlc_grid_{filename}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "main"):
        raise RuntimeError(f"{filename} does not expose main()")

    module.main()


def main() -> None:
    if sys.platform.startswith("win"):
        _run_script("vlc_grid.py")
        return

    try:
        _run_script("vlc_grid (1).py")
    except Exception:
        _run_script("vlc_grid.py")


if __name__ == "__main__":
    main()
