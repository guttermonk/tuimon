#!/usr/bin/env python3
# ----------------------------------------------------------------------------
# WAYBAR CPU MODULE
# ----------------------------------------------------------------------------
# CPU monitoring script for waybar.
# Features:
# - Per-core usage visualization (Die layout)
# - Power usage (RAPL)
# - Temperature monitoring
# - Top processes consuming CPU
# ----------------------------------------------------------------------------

import json
import psutil
import subprocess
import re
import os
import time
import shutil
import pickle
from collections import deque

import pathlib
import glob
import argparse

# ---------------------------------------------------
# ARGUMENTS
# ---------------------------------------------------
parser = argparse.ArgumentParser(description='Waybar CPU monitor')
parser.add_argument('--display', choices=['temp', 'percent', 'both'], default='temp',
                    help='What to display: temp, percent, or both')
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
CPU_ICON_GENERAL = "󰻠"  # nf-md-cpu_64_bit U+F0EE0
HISTORY_FILE = "/tmp/waybar_cpu_history.pkl"
HISTORY_SIZE = 50  # Number of data points to keep in history

def strip_pango(text):
    """Strip Pango markup tags to get plain text length."""
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
try:
    import tomllib
except ImportError:
    tomllib = None

def load_theme_colors():
    theme_path = pathlib.Path.home() / ".config/waybar/colors.toml"
    defaults = {
        "black": "#000000", "red": "#ff0000", "green": "#00ff00", "yellow": "#ffff00",
        "blue": "#0000ff", "magenta": "#ff00ff", "cyan": "#00ffff", "white": "#ffffff",
        "bright_black": "#555555", "bright_red": "#ff5555", "bright_green": "#55ff55",
        "bright_yellow": "#ffff55", "bright_blue": "#5555ff", "bright_magenta": "#ff55ff",
        "bright_cyan": "#55ffff", "bright_white": "#ffffff"
    }
    headers = {}
    if not tomllib or not theme_path.exists(): return defaults, headers
    try:
        data = tomllib.loads(theme_path.read_text())
        colors = data.get("colors", {})
        normal = colors.get("normal", {})
        bright = colors.get("bright", {})
        headers = colors.get("headers", {})
        return {**defaults, **normal, **{f"bright_{k}": v for k, v in bright.items()}}, headers
    except Exception: return defaults, headers

COLORS, HEADER_COLORS = load_theme_colors()
cpu_header_color = HEADER_COLORS.get("cpu", COLORS["red"])
SECTION_COLORS = {"CPU": {"icon": cpu_header_color, "text": cpu_header_color}}

COLOR_TABLE = [
    {"color": COLORS["blue"],           "cpu_gpu_temp": (0, 35),   "cpu_power": (0.0, 30)},
    {"color": COLORS["cyan"],           "cpu_gpu_temp": (36, 45),  "cpu_power": (31.0, 60)},
    {"color": COLORS["green"],          "cpu_gpu_temp": (46, 54),  "cpu_power": (61.0, 90)},
    {"color": COLORS["yellow"],         "cpu_gpu_temp": (55, 65),  "cpu_power": (91.0, 120)},
    {"color": COLORS["bright_yellow"],  "cpu_gpu_temp": (66, 75),  "cpu_power": (121.0,150)},
    {"color": COLORS["bright_red"],     "cpu_gpu_temp": (76, 85),  "cpu_power": (151.0,180)},
    {"color": COLORS["red"],            "cpu_gpu_temp": (86, 999), "cpu_power": (181.0,999)}
]

def get_color(value, metric_type):
    if value is None: return "#ffffff"
    try: value = float(value)
    except ValueError: return "#ffffff"
    for entry in COLOR_TABLE:
        if metric_type in entry:
            low, high = entry[metric_type]
            if low <= value <= high: return entry["color"]
    return COLOR_TABLE[-1]["color"]

# ---------------------------------------------------
# HARDWARE DETECTION
# ---------------------------------------------------
def get_cpu_name():
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    name = line.split(":")[1].strip()
                    # Remove trademark symbols and redundant "CPU"
                    name = name.replace("(R)", "").replace("(TM)", "").replace(" CPU", "")
                    # Clean up extra spaces
                    name = " ".join(name.split())
                    return name
    except Exception:
        pass
    return "Unknown CPU"

def get_rapl_path():
    # Find the energy_uj file for package-0 (CPU)
    base = "/sys/class/powercap"
    if not os.path.exists(base): return None
    
    # Search for intel-rapl or similar directories
    # Usually intel-rapl:0 is the package
    paths = glob.glob(f"{base}/*/energy_uj")
    for p in paths:
        if "intel-rapl:0" in p or "package" in p:
            return p
    # Fallback to first found
    return paths[0] if paths else None

# ---------------------------------------------------
# HISTORY
# ---------------------------------------------------
def load_history():
    try:
        with open(HISTORY_FILE, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return {'cpu': deque(maxlen=HISTORY_SIZE), 'per_core': {}}

def save_history(cpu_hist, per_core_hist):
    try:
        with open(HISTORY_FILE, 'wb') as f:
            pickle.dump({'cpu': cpu_hist, 'per_core': per_core_hist}, f)
    except Exception:
        pass

# ---------------------------------------------------
# MAIN LOGIC
# ---------------------------------------------------
history = load_history()
cpu_history = history.get('cpu', deque(maxlen=HISTORY_SIZE))
per_core_history = history.get('per_core', {})

cpu_name = get_cpu_name()
max_cpu_temp = 0

# Temperature
try:
    temps = psutil.sensors_temperatures() or {}
    # Try common labels
    for label in ["k10temp", "coretemp", "zenpower"]:
        if label in temps:
            for t in temps[label]:
                if t.current > max_cpu_temp:
                    max_cpu_temp = int(t.current)
except Exception:
    pass

# Frequency
current_freq = max_freq = 0
try:
    cpu_info = psutil.cpu_freq(percpu=False)
    if cpu_info:
        current_freq = cpu_info.current or 0
        max_freq = cpu_info.max or 0
except Exception:
    pass

# Power (RAPL)
# Reading RAPL energy_uj requires elevated privileges on most systems.
# We use sudo -n (non-interactive) to avoid blocking on password prompt.
# Requires sudo NOPASSWD for cat on the energy_uj file.
def read_rapl_energy(path):
    """Read RAPL energy value, trying direct access first, then sudo."""
    # Try direct read first (works if udev rule set permissions)
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except PermissionError:
        pass
    # Fallback to sudo
    # Use /run/current-system/sw/bin/cat to match NixOS sudoers rule
    try:
        result = subprocess.run(
            ["sudo", "-n", "/run/current-system/sw/bin/cat", path],
            text=True, capture_output=True, timeout=1, stdin=subprocess.DEVNULL
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return None

cpu_power = 0.0
rapl_path = get_rapl_path()
if rapl_path:
    try:
        energy1 = read_rapl_energy(rapl_path)
        if energy1 is not None:
            time.sleep(0.05)
            energy2 = read_rapl_energy(rapl_path)
            if energy2 is not None:
                delta = energy2 - energy1
                # Handle overflow
                if delta < 0: 
                    # Try to find max range
                    max_f = os.path.join(os.path.dirname(rapl_path), "max_energy_range_uj")
                    max_e = read_rapl_energy(max_f)
                    if max_e is not None:
                        delta = (max_e + energy2) - energy1
                    else:
                        delta = (2**32 + energy2) - energy1
                
                cpu_power = (delta / 1_000_000) / 0.05
    except Exception:
        pass

cpu_percent = psutil.cpu_percent(interval=0.1)
cpu_history.append(cpu_percent)

# Per Core
per_core = psutil.cpu_percent(interval=0.1, percpu=True)
decay_factor = 0.95
for i, usage in enumerate(per_core):
    if i not in per_core_history:
        per_core_history[i] = usage
    else:
        per_core_history[i] = (per_core_history[i] * decay_factor) + (usage * (1 - decay_factor))

def get_core_color(usage):
    if usage < 20: return "#81c8be"
    elif usage < 40: return "#a6d189"
    elif usage < 60: return "#e5c890"
    elif usage < 80: return "#ef9f76"
    elif usage < 95: return "#ea999c"
    else: return "#e78284"

# ---------------------------------------------------
# TOOLTIP
# ---------------------------------------------------
header_line = (
    f"<span foreground='{SECTION_COLORS['CPU']['icon']}'>{CPU_ICON_GENERAL}</span> "
    f"<span foreground='{SECTION_COLORS['CPU']['text']}'>CPU</span> - {cpu_name}"
)
tooltip_lines = []

cpu_rows = [
    ("󱐋", f"Clock Speed: {span(f'{current_freq/1000:>5.2f}GHz', get_color((current_freq/max_freq*100) if max_freq > 0 else 0, 'cpu_power'))} / {max_freq/1000:.2f}GHz"),
    ("󰔏", f"Temperature: {span(f'{max_cpu_temp:>3}°C', get_color(max_cpu_temp,'cpu_gpu_temp'))}"),
    ("󰚥", f"Power: {span(f'{cpu_power:>6.1f}W', get_color(cpu_power,'cpu_power'))}"),
    ("󰓅", f"Utilization: {span(f'{cpu_percent:>3.0f}%', get_color(cpu_percent,'cpu_power'))}")
]

for icon, text_row in cpu_rows:
    tooltip_lines.append(f"{icon} {text_row}")

# Build non-graphic content first for width calculation
non_graphic_lines = list(tooltip_lines)  # Copy current lines

# Active Cores (using smoothed history for 10 min avg approximation)
active_core_lines = []
active_core_lines.append("")
active_core_lines.append("Active Cores (10 min Avg):")
# Sort cores by their smoothed average usage
sorted_cores = sorted(per_core_history.items(), key=lambda x: x[1], reverse=True)
active_count = 0
for core_idx, avg_usage in sorted_cores:
    if active_count >= 3: break
    if avg_usage >= 5:  # Only show cores with at least 5% average usage
        core_num = core_idx + 1
        color = get_core_color(avg_usage)
        active_core_lines.append(f" • Core {core_num:02d}:       {span(f'󰘚 {avg_usage:>5.1f}% used', color)}")
        active_count += 1
if active_count == 0:
    active_core_lines.append(f" • {span('All cores idle', COLORS['bright_black'])}")

# Top Processes
process_lines = []
process_lines.append("")
process_lines.append("Top CPU Processes:")
try:
    ps_cmd = ["ps", "-eo", "pcpu,comm,args", "--sort=-pcpu", "--no-headers"]
    ps_output = subprocess.check_output(ps_cmd, text=True).strip()
    count = 0
    for line in ps_output.split('\n'):
        if count >= 5: break
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 2:
            try:
                usage = float(parts[0])
                name = parts[1]
                if "waybar" in parts[2] if len(parts)>2 else "": continue
                if len(name) > 18: name = name[:17] + "…"
                color = get_core_color(usage)
                # Estimate cores used (100% = 1 core)
                cores_used = max(1, int(round(usage / 100)))
                core_text = "core" if cores_used == 1 else "cores"
                process_lines.append(f" • {name:<18} {span(f'{usage:>6.1f}% ({cores_used} {core_text})', color)}")
                count += 1
            except Exception:
                continue
except Exception:
    pass

# Calculate tooltip width BEFORE adding graphics (based on non-graphic content)
# Header is rendered at size 14000, body at size 11000, so scale header width accordingly
all_non_graphic = non_graphic_lines + active_core_lines + process_lines
body_width = calc_tooltip_width(all_non_graphic)
header_width = int(len(strip_pango(header_line)) * 14000 / 11000)
tooltip_width = max(body_width, header_width)

# CPU Die Visualization - calculate centering
cpu_viz_width = 21  # Plain text width of the graphic
center_padding = " " * max(0, (tooltip_width - cpu_viz_width) // 2)

substrate_color = get_color(max_cpu_temp, 'cpu_gpu_temp')
border_color = COLORS['white']

# Build centered graphic
tooltip_lines.append("")
tooltip_lines.append(f"{center_padding}<span foreground='{border_color}'>╭──┘└────┘⠿└─────┘└─╮</span>")
tooltip_lines.append(f"{center_padding}<span foreground='{border_color}'>┘</span><span foreground='{substrate_color}'>░░░░░░░░░░░░░░░░░░░</span><span foreground='{border_color}'>└</span>")

# Grid layout for cores (adjust rows/cols based on core count if needed, fixed 6x4 here)
row_patterns = [("┐", "┌"), ("│", "│"), ("┘", "└")] * 2
for row in range(6):
    start_char, end_char = row_patterns[row]
    line_parts = [f"{center_padding}<span foreground='{border_color}'>{start_char}</span><span foreground='{substrate_color}'>░░</span>"]
    for col in range(4):
        core_idx = row * 4 + col
        if core_idx < len(per_core):
            usage = per_core[core_idx]
            color = get_core_color(usage)
            circle = "●" if usage >= 10 else "○"
            line_parts.append(f"<span foreground='{border_color}'>[</span><span foreground='{color}'>{circle}</span><span foreground='{border_color}'>]</span>")
        else:
            line_parts.append(f"<span foreground='{substrate_color}'>░░░</span>")
        if col < 3: line_parts.append(f"<span foreground='{substrate_color}'>░</span>")
    line_parts.append(f"<span foreground='{substrate_color}'>░░</span><span foreground='{border_color}'>{end_char}</span>")
    tooltip_lines.append("".join(line_parts))

tooltip_lines.append(f"{center_padding}<span foreground='{border_color}'>┐</span><span foreground='{substrate_color}'>░░░░░░░░░░░░░░░░░░░</span><span foreground='{border_color}'>┌</span>")
tooltip_lines.append(f"{center_padding}<span foreground='{border_color}'>╰──┐┌────┐⣶┌─────┐┌─╯</span>")

# Add Active Cores and Process lines
tooltip_lines.extend(active_core_lines)
tooltip_lines.extend(process_lines)

# Insert top rule at beginning
tooltip_lines.insert(0, "─" * tooltip_width)

tooltip_lines.append("")
tooltip_lines.append(f"<span foreground='{COLORS['white']}'>{'┈' * tooltip_width}</span>")
tooltip_lines.append("󰍽 LMB: Btop")

save_history(cpu_history, per_core_history)

TERMINAL = os.environ.get("TERMINAL") or shutil.which("alacritty") or "xterm"
if os.environ.get("WAYBAR_CLICK_TYPE") == "left":
    subprocess.Popen([TERMINAL, "-e", "btop"])

# Build display text based on --display argument
temp_color = get_color(max_cpu_temp, 'cpu_gpu_temp')
percent_color = get_color(cpu_percent, 'cpu_power')

if args.display == "temp":
    display_text = f"{CPU_ICON_GENERAL} {text_span(f'{max_cpu_temp}°C', temp_color)}"
elif args.display == "percent":
    display_text = f"{CPU_ICON_GENERAL} {text_span(f'{cpu_percent:.0f}%', percent_color)}"
else:  # both
    display_text = f"{CPU_ICON_GENERAL} {text_span(f'{max_cpu_temp}°C', temp_color)} {text_span(f'{cpu_percent:.0f}%', percent_color)}"

print(json.dumps({
    "text": display_text,
    "tooltip": f"<span size='14000'>{header_line}</span>\n<span size='11000'>{chr(10).join(tooltip_lines)}</span>",
    "markup": "pango",
    "class": "cpu",
    "click-events": True
}))
