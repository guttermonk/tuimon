#!/usr/bin/env python3
"""
Theme colors for the monitors, loaded from a base file plus an optional overlay.

Every monitor used to carry its own copy of this loader -- four copies that had
already drifted apart in whitespace and comments while staying identical in
behavior. They are one function now, so a change here reaches all of them.

Two files, the second winning:

    ~/.config/waybar/colors.toml                             the palette
    ${XDG_CACHE_HOME:-~/.cache}/wallust/colors-tuimon.toml   wallpaper colors

The base is what the home-manager module writes, and on its own it is the whole
story. The overlay exists for tools that recolor the desktop from the current
wallpaper at run time -- wallust, pywal -- which rewrite their output on every
wallpaper change and cannot be expected to own a config file. Such a tool
renders the overlay; the monitors pick it up on their next tick, with no restart
and nothing to reload. Point TUIMON_COLORS_OVERLAY somewhere else if your tool
writes its colors under a different name.

The merge is per key, so an overlay carrying only `[colors.normal]` leaves the
base's bright set and header mapping alone. An absent or empty overlay is the
normal state and means "use the palette", which is what every theme that is not
wallpaper-derived wants.
"""

import os
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


def overlay_path():
    override = os.environ.get("TUIMON_COLORS_OVERLAY")
    if override:
        return pathlib.Path(override)
    cache = os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")
    return pathlib.Path(cache) / "wallust/colors-tuimon.toml"


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

    Called on every tick rather than cached at import, which is what lets a
    palette change reach a running monitor -- including under tuimon-loop.py,
    whose whole design is to keep the interpreter warm across ticks.
    """
    normal, bright, headers = _read(base_path())
    o_normal, o_bright, o_headers = _read(overlay_path())

    normal = {**normal, **o_normal}
    bright = {**bright, **o_bright}
    headers = {**headers, **o_headers}

    colors = {**DEFAULTS, **normal, **{f"bright_{k}": v for k, v in bright.items()}}
    return colors, headers
