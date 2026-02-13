#!/usr/bin/env python3
# ----------------------------------------------------------------------------
# WAYBAR MEMORY MODULE
# ----------------------------------------------------------------------------
# A dynamic memory monitor for Waybar.
# Features:
# - Real-time RAM usage with color-coded states
# - Tooltip with detailed breakdown (Used, Cached, Buffers)
# - Auto-detects memory modules via dmidecode (requires sudo permissions)
# - Temperature monitoring (requires lm_sensors)
# ----------------------------------------------------------------------------

import json
import psutil
import subprocess
import pathlib
import argparse
import os
import shutil

# ---------------------------------------------------
# ARGUMENTS
# ---------------------------------------------------
parser = argparse.ArgumentParser(description='Waybar Memory monitor')
parser.add_argument('--plain', action='store_true',
                    help='Output plain text without Pango color markup (for CSS styling)')
args = parser.parse_args()

def span(text, color):
    """Wrap text in a Pango span with foreground color (for tooltips - always colored)."""
    return f"<span foreground='{color}'>{text}</span>"

def text_span(text, color):
    """Wrap text in a Pango span for waybar text output, or return plain text if --plain."""
    if args.plain:
        return str(text)
    return f"<span foreground='{color}'>{text}</span>"

# ---------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------
MEM_ICON = ""  # nf-fa-memory U+EFC5

def strip_pango(text):
    """Strip Pango markup tags to get plain text length."""
    import re
    return re.sub(r'<[^>]+>', '', text)

def calc_tooltip_width(lines, min_width=30):
    """Calculate tooltip width based on longest line."""
    max_len = min_width
    for line in lines:
        # Handle lines with embedded newlines (like graphics)
        for subline in str(line).split('\n'):
            plain_len = len(strip_pango(subline))
            if plain_len > max_len:
                max_len = plain_len
    return max_len

# ---------------------------------------------------
# THEME & COLORS
# ---------------------------------------------------
# Attempts to load colors from a TOML theme file.
# Defaults to a standard palette if the file is missing.
try:
    import tomllib
except ImportError:
    tomllib = None

def load_theme_colors():
    # UPDATE THIS PATH to your specific theme file if you have one
    theme_path = pathlib.Path.home() / ".config/waybar/colors.toml"
    
    defaults = {
        "black": "#000000", "red": "#ff0000", "green": "#00ff00", "yellow": "#ffff00",
        "blue": "#0000ff", "magenta": "#ff00ff", "cyan": "#00ffff", "white": "#ffffff",
        "bright_black": "#555555", "bright_red": "#ff5555", "bright_green": "#55ff55",
        "bright_yellow": "#ffff55", "bright_blue": "#5555ff", "bright_magenta": "#ff55ff",
        "bright_cyan": "#55ffff", "bright_white": "#ffffff"
    }

    headers = {}
    if not tomllib or not theme_path.exists():
        return defaults, headers

    try:
        data = tomllib.loads(theme_path.read_text())
        colors = data.get("colors", {})
        normal = colors.get("normal", {})
        bright = colors.get("bright", {})
        headers = colors.get("headers", {})
        
        # Merge loaded colors with defaults
        return {**defaults, **normal, **{f"bright_{k}": v for k, v in bright.items()}}, headers
    except Exception:
        return defaults, headers

COLORS, HEADER_COLORS = load_theme_colors()
memory_header_color = HEADER_COLORS.get("memory", COLORS["green"])

SECTION_COLORS = {
    "Memory":  {"icon": memory_header_color,  "text": memory_header_color},
}

# Color thresholds for metrics
COLOR_TABLE = [
    {"color": COLORS["blue"],           "mem_storage": (0.0, 10), "mem_temp": (0, 40)},
    {"color": COLORS["cyan"],           "mem_storage": (10.0, 20), "mem_temp": (41, 50)},
    {"color": COLORS["green"],          "mem_storage": (20.0, 40), "mem_temp": (51, 60)},
    {"color": COLORS["yellow"],         "mem_storage": (40.0, 60), "mem_temp": (61, 70)},
    {"color": COLORS["bright_yellow"],  "mem_storage": (60.0, 80), "mem_temp": (71, 75)},
    {"color": COLORS["bright_red"],     "mem_storage": (80.0, 90), "mem_temp": (76, 80)},
    {"color": COLORS["red"],            "mem_storage": (90.0,100), "mem_temp": (81, 999)}
]

def get_color(value, metric_type):
    if value is None: return "#ffffff"
    try:
        value = float(value)
    except ValueError: return "#ffffff"
    
    for entry in COLOR_TABLE:
        if metric_type in entry:
            low, high = entry[metric_type]
            if low <= value <= high:
                return entry["color"]
    return COLOR_TABLE[-1]["color"]

# ---------------------------------------------------
# HARDWARE DETECTION
# ---------------------------------------------------
def get_memory_temps():
    """
    Reads memory temperatures from lm_sensors.
    Requires: lm_sensors installed and sensors-detect run.
    
    Supports:
    - jc42/spd/dram sensors (standard DIMM temperature sensors)
    - applesmc memory proximity sensors (TM0P, Tm0P on MacBooks)
    """
    temps = []
    try:
        output = subprocess.check_output(["sensors", "-j"], text=True, stderr=subprocess.DEVNULL)
        data = json.loads(output)
        
        # First, try standard memory sensors (jc42, spd, dram)
        for chip, content in data.items():
            if any(x in chip for x in ["jc42", "spd", "dram"]):
                for feature, subfeatures in content.items():
                    if isinstance(subfeatures, dict):
                        for key, val in subfeatures.items():
                            if "input" in key:
                                temps.append(int(val))
        
        # If no standard sensors found, try applesmc memory proximity sensors
        if not temps:
            for chip, content in data.items():
                if "applesmc" in chip.lower():
                    if isinstance(content, dict):
                        # Look for memory-related sensors: TM0P (Memory Proximity), Tm0P
                        for feature, subfeatures in content.items():
                            # TM0P = Memory Slot Proximity, Tm0P = Memory Controller
                            if feature in ["TM0P", "Tm0P", "TM0S", "TM1P", "TM1S"]:
                                if isinstance(subfeatures, dict):
                                    for key, val in subfeatures.items():
                                        if "input" in key and isinstance(val, (int, float)) and val > 0:
                                            temps.append(int(val))
    except Exception:
        pass
    return temps

def get_memory_modules_from_dmidecode():
    """
    Fetches RAM stick details.
    NOTE: Requires sudo permissions for dmidecode without password.
    On NixOS, use security.sudo.extraRules or enable the waybar-system-monitors NixOS module.
    """
    detected_modules = []
    real_temps = get_memory_temps()
    try:
        output = subprocess.check_output(["sudo", "-n", "dmidecode", "--type", "memory"], text=True, stderr=subprocess.PIPE)
        
        current_module = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Memory Device"):
                if current_module and current_module.get("size") and current_module["size"] != "No Module Installed":
                    detected_modules.append(current_module)
                    
                t_val = real_temps[len(detected_modules)] if len(detected_modules) < len(real_temps) else 0
                current_module = {"temp": t_val}
            elif current_module:
                if line.startswith("Locator:"):
                    current_module["label"] = line.split(":", 1)[1].strip()
                elif line.startswith("Size:"):
                    size_str = line.split(":", 1)[1].strip()
                    if "MB" in size_str:
                        try:
                            size_mb = int(size_str.replace("MB", "").strip())
                            if size_mb >= 1024:
                                current_module["size"] = f"{size_mb // 1024}GB"
                            else:
                                current_module["size"] = size_str
                        except ValueError:
                            current_module["size"] = size_str
                    else:
                        current_module["size"] = size_str
                elif line.startswith("Type:"):
                    current_module["type"] = line.split(":", 1)[1].strip()
                elif line.startswith("Speed:"):
                    speed_str = line.split(":", 1)[1].strip()
                    if "MT/s" in speed_str:
                        current_module["speed"] = speed_str.replace("MT/s", "MHz")
                    else:
                        current_module["speed"] = speed_str
        
        if current_module and current_module.get("size") and current_module["size"] != "No Module Installed":
            detected_modules.append(current_module)

    except Exception:
        # Fail silently or return empty if sudo not configured
        return []
    
    return detected_modules

# ---------------------------------------------------
# MAIN LOGIC
# ---------------------------------------------------
mem = psutil.virtual_memory()
mem_used_gb = mem.used / (1024**3)
mem_total_gb = mem.total / (1024**3)
mem_percent = mem.percent
mem_available_gb = mem.available / (1024**3)
mem_cached_gb = mem.cached / (1024**3) if hasattr(mem, 'cached') else 0
mem_buffers_gb = mem.buffers / (1024**3) if hasattr(mem, 'buffers') else 0

# ---------------------------------------------------
# TOOLTIP
# ---------------------------------------------------
header_line = (
    f"<span foreground='{SECTION_COLORS['Memory']['icon']}'>{MEM_ICON}</span>  "
    f"<span foreground='{SECTION_COLORS['Memory']['text']}'>Memory</span>"
)
tooltip_lines = []
tooltip_lines.append(f"󰓅 Usage: {span(f'{mem_used_gb:>3.0f}GB', get_color(mem_percent,'mem_storage'))} / {mem_total_gb:.0f}GB")

memory_modules = get_memory_modules_from_dmidecode()
memory_temps = get_memory_temps()  # Get temps separately for fallback

# Module Table
if memory_modules:
    rows = []
    for mod in memory_modules:
        t_val = mod.get('temp', 0)
        rows.append({
            "icon": MEM_ICON,
            "label": mod.get("label", "DIMM"),
            "size": mod.get("size", "N/A"),
            "speed": mod.get("speed", "N/A"),
            "type": mod.get("type", "DDR4"),
            "temp_text": f"{t_val}°C",
            "temp_val": t_val
        })

    # Calculate column widths
    w_label = max(len(r["label"]) for r in rows)
    w_size = max(len(r["size"]) for r in rows)
    w_speed = max(len(r["speed"]) for r in rows)
    w_type = max(len(r["type"]) for r in rows)
    w_temp = max(len(r["temp_text"]) for r in rows)

    tooltip_lines.append("")
    for r in rows:
        temp_colored = span(f"{r['temp_text']:>{w_temp}}", get_color(r['temp_val'], 'mem_temp'))
        line = (
            f"{r['icon']} {r['label']:<{w_label}}, "
            f"{r['size']:<{w_size}}, "
            f"{r['type']:<{w_type}}, "
            f"{r['speed']:<{w_speed}}, "
            f"{temp_colored}"
        )
        tooltip_lines.append(line)

# If no module details but we have temps, show a simple temperature line
elif memory_temps:
    avg_temp = sum(memory_temps) // len(memory_temps)
    max_temp = max(memory_temps)
    temp_color = get_color(max_temp, 'mem_temp')
    tooltip_lines.append(f"󰔏 Temperature: {span(f'{max_temp:>3}°C', temp_color)}")

tooltip_lines.append("")

# Calculate max temp for connectors
max_mem_temp = 0
if memory_modules:
    max_mem_temp = max(m.get('temp', 0) for m in memory_modules)
elif memory_temps:
    max_mem_temp = max(memory_temps)

connector_color = get_color(max_mem_temp, 'mem_temp')
frame_color = COLORS['white']

# Calculate percentages
# Note: mem.used includes cached+buffers, so we subtract them for "active" usage
cached_pct = (mem.cached / mem.total) * 100 if hasattr(mem, 'cached') else 0
buffers_pct = (mem.buffers / mem.total) * 100 if hasattr(mem, 'buffers') else 0
active_pct = ((mem.used - mem.cached - mem.buffers) / mem.total) * 100 if hasattr(mem, 'cached') else (mem.used / mem.total) * 100
active_pct = max(0, active_pct)  # Ensure non-negative
free_pct = (mem.free / mem.total) * 100

# Build legend first to determine graphic width
legend_plain = f"█ Active {active_pct:>5.1f}%  █ Buffers {buffers_pct:>5.1f}%  █ Cached {cached_pct:>5.1f}%  █ Free {free_pct:>5.1f}%"
graphic_base_width = len(legend_plain)

legend = (
    f"<span size='11000'>"
    f"{span('█', COLORS['red'])} Active {active_pct:>5.1f}%  "
    f"{span('█', COLORS['cyan'])} Buffers {buffers_pct:>5.1f}%  "
    f"{span('█', COLORS['yellow'])} Cached {cached_pct:>5.1f}%  "
    f"{span('█', '#555555')} Free {free_pct:>5.1f}%"
    f"</span>"
)

# Calculate tooltip width BEFORE adding graphics (based on non-graphic content)
# Header is rendered at size 14000, body at size 11000, so scale header width accordingly
body_width = calc_tooltip_width(tooltip_lines + [legend])
header_width = int(len(strip_pango(header_line)) * 14000 / 11000)
tooltip_width = max(body_width, header_width)

# Graphic Dimensions
graph_width = graphic_base_width - 2
inner_width = graph_width - 4
bar_len = inner_width - 2

# Calculate centering padding based on tooltip width
center_padding = " " * max(0, (tooltip_width - graphic_base_width) // 2)
# Internal graphic padding (for the slightly narrower frame)
internal_padding = " " * int((graphic_base_width - graph_width) // 2)

def c(text, color):
    return f"<span foreground='{color}'>{text}</span>"

# Build graphic lines with centering
# Line 1
tooltip_lines.append(f"{center_padding}{internal_padding} {c('╭' + '─'*inner_width + '╮', frame_color)}")
# Line 2
tooltip_lines.append(f"{center_padding}{internal_padding}{c('╭╯', frame_color)}{c('░'*inner_width, connector_color)}{c('╰╮', frame_color)}")
# Line 3 (Bar)
c_used = int((active_pct / 100.0) * bar_len)
c_cached = int((cached_pct / 100.0) * bar_len)
c_buffers = int((buffers_pct / 100.0) * bar_len)
# Clamp segments to prevent overflow - reduce buffers/cached first if needed
total_used = c_used + c_cached + c_buffers
if total_used > bar_len:
    overflow = total_used - bar_len
    # Reduce from buffers first, then cached
    reduce_buffers = min(c_buffers, overflow)
    c_buffers -= reduce_buffers
    overflow -= reduce_buffers
    if overflow > 0:
        c_cached = max(0, c_cached - overflow)
c_free = bar_len - c_used - c_cached - c_buffers
if c_free < 0: c_free = 0

bar_str = (
    f"{span('█' * c_used, COLORS['red'])}"
    f"{span('█' * c_buffers, COLORS['cyan'])}"
    f"{span('█' * c_cached, COLORS['yellow'])}"
    f"{span('█' * c_free, '#555555')}"
)
tooltip_lines.append(f"{center_padding}{internal_padding}{c('╰╮', frame_color)}{c('░', connector_color)}{bar_str}{c('░', connector_color)}{c('╭╯', frame_color)}")
# Line 4
tooltip_lines.append(f"{center_padding}{internal_padding} {c('│', frame_color)}{c('░'*inner_width, connector_color)}{c('│', frame_color)}")
# Line 5
tooltip_lines.append(f"{center_padding}{internal_padding}{c('╭╯', frame_color)}{c('┌' + '┬'*bar_len + '┐', frame_color)}{c('╰╮', frame_color)}")
# Line 6
tooltip_lines.append(f"{center_padding}{internal_padding}{c('└─', frame_color)}{c('┴'*inner_width, frame_color)}{c('─┘', frame_color)}")

# Center the legend too
legend_centered = f"{center_padding}{legend}"

# Insert top rule at beginning
tooltip_lines.insert(0, "─" * tooltip_width)

# Add separator before legend, then legend
tooltip_lines.append("─" * tooltip_width)
tooltip_lines.append(legend_centered)

# Top Memory Processes
tooltip_lines.append("")
tooltip_lines.append("Top Memory Processes:")
try:
    ps_cmd = ["ps", "-eo", "pmem,rss,comm", "--sort=-pmem", "--no-headers"]
    ps_output = subprocess.check_output(ps_cmd, text=True).strip()
    count = 0
    for line in ps_output.split('\n'):
        if count >= 5: break
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 3:
            try:
                mem_pct = float(parts[0])
                rss_kb = int(parts[1])
                name = parts[2]
                if mem_pct < 0.1: continue  # Skip negligible processes
                if len(name) > 18: name = name[:17] + "…"
                # Convert RSS to human readable
                if rss_kb >= 1024 * 1024:
                    mem_str = f"{rss_kb / (1024 * 1024):.1f}GB"
                elif rss_kb >= 1024:
                    mem_str = f"{rss_kb / 1024:.0f}MB"
                else:
                    mem_str = f"{rss_kb}KB"
                color = get_color(mem_pct, 'mem_storage')
                tooltip_lines.append(f" • {name:<18} {span(f'{mem_pct:>5.1f}% ({mem_str})', color)}")
                count += 1
            except Exception:
                continue
    if count == 0:
        tooltip_lines.append(f" • {span('No significant memory usage', COLORS['bright_black'])}")
except Exception:
    pass

tooltip_lines.append("")
tooltip_lines.append(f"<span foreground='{COLORS['white']}'>{'┈' * tooltip_width}</span>")
tooltip_lines.append("󰍽 LMB: Btop")

# Handle click events
TERMINAL = os.environ.get("TERMINAL") or shutil.which("alacritty") or "xterm"
if os.environ.get("WAYBAR_CLICK_TYPE") == "left":
    subprocess.Popen([TERMINAL, "-e", "btop"])

print(json.dumps({
    "text": f"{text_span(MEM_ICON, get_color(mem_percent,'mem_storage'))} {text_span(f'{mem_percent:.0f}%', get_color(mem_percent,'mem_storage'))}",
    "tooltip": f"<span size='14000'>{header_line}</span>\n<span size='11000'>{chr(10).join(tooltip_lines)}</span>",
    "markup": "pango",
    "class": "memory",
    "click-events": True
}))
