"""Absolute asset paths independent of launcher working directory."""
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parent / 'assets'


def asset_path(relative):
    return ASSET_ROOT / relative
