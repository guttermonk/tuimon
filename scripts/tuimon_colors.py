#!/usr/bin/env python3
"""
Theme colors for the monitors, read from ~/.config/waybar/colors.toml.

Every monitor used to carry its own copy of this loader -- four copies that had
already drifted apart in whitespace and comments while staying identical in
behavior. They are one function now, so a change here reaches all of them.
"""

import pathlib

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

# Visible, deliberately ugly fallbacks: if these reach the bar, no color file
# was readable and that should look like a fault rather than a palette.
DEFAULTS = {
    "black": "#000000", "red": "#ff0000", "green": "#00ff00", "yellow": "#ffff00",
    "blue": "#0000ff", "magenta": "#ff00ff", "cyan": "#00ffff", "white": "#ffffff",
    "bright_black": "#555555", "bright_red": "#ff5555", "bright_green": "#55ff55",
    "bright_yellow": "#ffff55", "bright_blue": "#5555ff", "bright_magenta": "#ff55ff",
    "bright_cyan": "#55ffff", "bright_white": "#ffffff"
}


def base_path():
    return pathlib.Path.home() / ".config/waybar/colors.toml"


def _read(path):
    """The three color tables in one file, or three empty ones."""
    if not tomllib:
        return {}, {}, {}
    try:
        if not path.exists():
            return {}, {}, {}
        colors = tomllib.loads(path.read_text()).get("colors", {})
    # Unreadable or malformed is not worth taking the module down for: the bar
    # would lose a reading rather than a color. Fall through to what is left.
    except Exception:
        return {}, {}, {}
    return colors.get("normal", {}), colors.get("bright", {}), colors.get("headers", {})


def load_theme_colors():
    """(colors, headers), with `bright` flattened to bright_<name> keys.

    Called on every tick rather than cached at import, which is what lets an
    edit to the file reach a running monitor -- including under tuimon-loop.py,
    whose whole design is to keep the interpreter warm across ticks.
    """
    normal, bright, headers = _read(base_path())
    colors = {**DEFAULTS, **normal, **{f"bright_{k}": v for k, v in bright.items()}}
    return colors, headers
