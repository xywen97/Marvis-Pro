"""Separate shipped read-only resources from per-user mutable application data."""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bundled():
    return (ROOT / 'bundle.json').is_file()


def user_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'MarvisXY'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'MarvisXY'


def default_directory():
    return user_directory() if bundled() else ROOT / '.marvis'


def default_config():
    if not bundled(): return ROOT / 'config.yaml'
    directory = user_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / 'config.yaml'
    # Exclusive creation preserves the user's configuration across upgrades.
    try:
        with target.open('x', encoding='utf-8') as file:
            file.write((ROOT / 'config.yaml').read_text(encoding='utf-8'))
    except FileExistsError: pass
    return target


def env_path():
    return user_directory() / '.env' if bundled() else ROOT / '.env'
