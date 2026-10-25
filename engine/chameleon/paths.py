"""Where the brand kit lives.

`resize()` is never told where the assets are, so we resolve the same
folders `engine/main.py` and the Dockerfile use. The env overrides exist for
the test harness, which renames assets to check we never lean on file names.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def assets_dir() -> Path:
    return Path(os.environ.get("CHAMELEON_ASSETS_DIR", REPO_ROOT / "brand_kit" / "assets"))


def fonts_dir() -> Path:
    return Path(os.environ.get("CHAMELEON_FONTS_DIR", REPO_ROOT / "brand_kit" / "fonts"))
