#!/usr/bin/env python3
# ----------------------------------------------------------------------------
# WAYBAR STORAGE MODULE
# ----------------------------------------------------------------------------
# Auto-detects mounted physical drives and displays usage in a sleek dashboard.
# Features:
# - Dynamic drive detection (ignores snaps, loops, etc.)
# - Real-time I/O speeds (Read/Write)
# - Drive temperature monitoring (requires lm_sensors/smartctl)
# - Health status via smartctl (requires sudo)
# ----------------------------------------------------------------------------

import json
import subprocess
import os
import glob
import psutil
import re
import time
import pickle

import pathlib
import argparse

# ---------------------------------------------------
# ARGUMENTS
# ---------------------------------------------------
parser = argparse.ArgumentParser(description='Waybar Storage monitor')
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
SSD_ICON = "󰋊"  # nf-md-harddisk U+F02CA
HDD_ICON = "󰋊"  # nf-md-harddisk U+F02CA
HISTORY_FILE = "/tmp/waybar_storage_history.pkl"

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
    # Tries to load from a standard waybar config location
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
        return {**defaults, **normal, **{f"bright_{k}": v for k, v in bright.items()}}, headers
    except Exception:
        return defaults, headers

COLORS, HEADER_COLORS = load_theme_colors()
storage_header_color = HEADER_COLORS.get("storage", COLORS["blue"])

SECTION_COLORS = {
    "Storage": {"icon": storage_header_color, "text": storage_header_color},
}

COLOR_TABLE = [
    {"color": COLORS["blue"],           "mem_storage": (0.0, 10),  "drive_temp": (0, 35)},
    {"color": COLORS["cyan"],           "mem_storage": (10.0, 20), "drive_temp": (36, 45)},
    {"color": COLORS["green"],          "mem_storage": (20.0, 40), "drive_temp": (46, 54)},
    {"color": COLORS["yellow"],         "mem_storage": (40.0, 60), "drive_temp": (55, 60)},
    {"color": COLORS["bright_yellow"],  "mem_storage": (60.0, 80), "drive_temp": (61, 70)},
    {"color": COLORS["bright_red"],     "mem_storage": (80.0, 90), "drive_temp": (71, 80)},
    {"color": COLORS["red"],            "mem_storage": (90.0,100), "drive_temp": (81, 999)}
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
# HISTORY & UTILS
# ---------------------------------------------------
def format_compact(val, suffix=""):
    """Format a value compactly with fixed width for consistent display."""
    if val is None: return f"{'0':>5}B{suffix}"
    try: val = float(val)
    except Exception: return f"{'0':>5}B{suffix}"
    if val < 1024: return f"{val:>5.0f}B{suffix}"
    val /= 1024
    if val < 1024: return f"{val:>5.1f}K{suffix}"
    val /= 1024
    if val < 1024: return f"{val:>5.1f}M{suffix}"
    val /= 1024
    return f"{val:>5.1f}G{suffix}"

def load_history():
    try:
        with open(HISTORY_FILE, 'rb') as f:
            data = pickle.load(f)
            if not isinstance(data, dict): return {'io': {}, 'timestamp': 0}
            return data
    except Exception: return {'io': {}, 'timestamp': 0}

def save_history(data):
    try:
        with open(HISTORY_FILE, 'wb') as f: pickle.dump(data, f)
    except Exception: pass

# ---------------------------------------------------
# HARDWARE SENSORS
# ---------------------------------------------------
def resolve_to_io_device(device_path):
    """
    Resolves a device path to the name used in disk_io_counters().
    For LUKS/LVM devices, this returns the dm-X name.
    """
    disk_name = os.path.basename(device_path)
    
    # Handle /dev/mapper/* devices (LUKS, LVM, etc.) - resolve symlink to get dm-X
    if device_path.startswith("/dev/mapper/") or "luks-" in disk_name:
        try:
            real_path = os.path.realpath(device_path)
            disk_name = os.path.basename(real_path)
        except Exception: pass
    
    return disk_name

def resolve_to_physical_disk(device_path):
    """
    Resolves a device path (including /dev/mapper/*, dm-*, etc.) to the underlying physical disk.
    Returns the base disk name (e.g., 'nvme0n1' or 'sda').
    """
    disk_name = resolve_to_io_device(device_path)
    
    # Handle dm-* devices - trace through slaves to find physical device
    while disk_name.startswith("dm-"):
        try:
            slaves_path = f"/sys/class/block/{disk_name}/slaves"
            slaves = os.listdir(slaves_path)
            if slaves:
                disk_name = slaves[0]
            else:
                break
        except Exception:
            break
    
    # Strip partition numbers to get base disk
    if disk_name.startswith("nvme"):
        disk_name = re.sub(r'p\d+$', '', disk_name)
    else:
        disk_name = re.sub(r'\d+$', '', disk_name)
    
    return disk_name

def get_drive_temp(mountpoint):
    """
    Attempts to find drive temperature via psutil -> device -> hwmon/smartctl.
    """
    try:
        partitions = psutil.disk_partitions()
        partition = next((p for p in partitions if p.mountpoint == mountpoint), None)
        if not partition: return None
        
        disk_name = resolve_to_physical_disk(partition.device)
        
        # 1. Try direct hwmon sysfs reading (works without sudo)
        # For NVMe drives, check /sys/class/hwmon/*/name for "nvme" and read temp
        for hwmon_path in glob.glob("/sys/class/hwmon/hwmon*"):
            try:
                with open(os.path.join(hwmon_path, "name")) as f:
                    name = f.read().strip()
                # Match NVMe drives or drives containing the disk name
                if name == "nvme" or disk_name in name:
                    temp_path = os.path.join(hwmon_path, "temp1_input")
                    if os.path.exists(temp_path):
                        with open(temp_path) as f:
                            return int(f.read().strip()) // 1000
            except Exception: pass
        
        # 2. Try block device hwmon symlink
        hwmon_block = f"/sys/class/block/{disk_name}/device/hwmon"
        if os.path.exists(hwmon_block):
            for hwmon in os.listdir(hwmon_block):
                temp_path = os.path.join(hwmon_block, hwmon, "temp1_input")
                if os.path.exists(temp_path):
                    try:
                        with open(temp_path) as f:
                            return int(f.read().strip()) // 1000
                    except Exception: pass
            
        # 3. Try sensors command (lm_sensors)
        try:
            output = subprocess.check_output(["sensors", "-j"], text=True, stderr=subprocess.DEVNULL)
            data = json.loads(output)
            # Heuristic search in sensors output
            for chip_name, chip_data in data.items():
                # Check for nvme or scsi/sata adapter keys that might match
                if disk_name in chip_name or (disk_name.startswith("nvme") and "nvme" in chip_name.lower()):
                    if isinstance(chip_data, dict):
                        for feature_name, feature_data in chip_data.items():
                            if isinstance(feature_data, dict):
                                # Look for temp input values
                                for key, val in feature_data.items():
                                    if "input" in key and isinstance(val, (int, float)):
                                        return int(val)
                            # Direct temp1_input at chip level
                            elif "temp" in feature_name and "input" in feature_name:
                                if isinstance(feature_data, (int, float)):
                                    return int(feature_data)
        except Exception: pass

        # 4. Fallback: smartctl (requires sudo NOPASSWD - use stdin=DEVNULL to avoid blocking)
        try:
            cmd = ["sudo", "-n", "smartctl", "-A", f"/dev/{disk_name}", "-j"]
            result = subprocess.run(cmd, text=True, capture_output=True, timeout=2, stdin=subprocess.DEVNULL)
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                # Try standard temperature field
                temp = data.get("temperature", {}).get("current")
                if temp:
                    return int(temp)
                # Try NVMe smart log
                nvme_log = data.get("nvme_smart_health_information_log", {})
                temp = nvme_log.get("temperature")
                if temp:
                    return int(temp)
        except Exception: pass

    except Exception:
        pass
    return None

# SMART health/wear changes on the order of hours, and `smartctl -a` is a
# heavy read through the drive's admin queue - don't run it every refresh.
SMART_CACHE_TTL = 3600

def get_smart_info(mountpoint, smart_cache):
    """
    Fetches basic health info via smartctl.
    Requires sudo NOPASSWD for smartctl, or will return N/A values.
    Results are cached in smart_cache (persisted via the history file) for
    SMART_CACHE_TTL seconds.
    """
    health, lifespan, tbw = "N/A", "N/A", "N/A"
    try:
        partitions = psutil.disk_partitions()
        partition = next((p for p in partitions if p.mountpoint == mountpoint), None)
        if not partition: return health, lifespan, tbw

        disk_name = resolve_to_physical_disk(partition.device)

        cached = smart_cache.get(disk_name)
        if cached and time.time() - cached.get("ts", 0) < SMART_CACHE_TTL:
            return tuple(cached["info"])

        # Use sudo -n (non-interactive) to avoid blocking on password prompt
        cmd = ["sudo", "-n", "smartctl", "-a", "-j", f"/dev/{disk_name}"]
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=2, stdin=subprocess.DEVNULL)
        if result.returncode == 0 and result.stdout:
            data = json.loads(result.stdout)
            passed = data.get("smart_status", {}).get("passed")
            health = "OK" if passed else "FAIL" if passed is False else "N/A"
            
            # NVMe specific
            if "nvme_smart_health_information_log" in data:
                nvme = data["nvme_smart_health_information_log"]
                used = nvme.get("percentage_used")
                if used is not None: lifespan = f"{max(0, 100 - used)}%"
                duw = nvme.get("data_units_written")
                if duw: tbw = f"{(duw * 512000) / 1e12:.1f}TB"
        # Cache N/A results too, so a missing smartctl/sudo setup doesn't
        # trigger the heavy call every refresh
        smart_cache[disk_name] = {"ts": time.time(), "info": [health, lifespan, tbw]}
    except Exception: pass
    return health, lifespan, tbw

# ---------------------------------------------------
# MAIN LOGIC
# ---------------------------------------------------
def get_drives():
    drives = []
    # Auto-detect physical drives
    for p in psutil.disk_partitions():
        # Allow /run/media and /media for USB drives, exclude other system paths
        if p.mountpoint.startswith('/run/media/') or p.mountpoint.startswith('/media/'):
            pass  # Allow these mountpoints
        elif any(x in p.mountpoint for x in ['/snap', '/boot', '/docker', '/var', '/run', '/sys', '/proc', '/dev', '/nix']): continue
        if any(x in p.device for x in ['/loop']): continue
        
        if p.fstype in ['ext4', 'btrfs', 'xfs', 'ntfs', 'vfat', 'apfs', 'zfs', 'exfat']:
            name = "Root" if p.mountpoint == "/" else os.path.basename(p.mountpoint)
            icon = SSD_ICON # Default icon
            drives.append((name, p.mountpoint, icon))
    return drives

def main():
    history = load_history()
    last_io = history.get('io', {})
    last_time = history.get('timestamp', 0)
    smart_cache = history.get('smart', {})
    if not isinstance(smart_cache, dict): smart_cache = {}
    current_time = time.time()
    
    try: current_io = psutil.disk_io_counters(perdisk=True)
    except Exception: current_io = {}

    drives = get_drives()
    storage_entries = []
    
    # Map mountpoints to device names for I/O lookup (resolve LUKS/dm-mapper to dm-X names)
    try:
        partitions = psutil.disk_partitions()
        mount_map = {p.mountpoint: resolve_to_io_device(p.device) for p in partitions}
    except Exception: mount_map = {}

    root_usage = 0

    for name, mountpoint, icon in drives:
        try:
            usage = psutil.disk_usage(mountpoint)
            used_pct = int(usage.percent)
            total_gb = usage.total / (1024**3)
            used_gb = usage.used / (1024**3)
            
            if mountpoint == "/": root_usage = used_pct
            
            temp = get_drive_temp(mountpoint)
            health, lifespan, tbw = get_smart_info(mountpoint, smart_cache)
            
            # I/O Speed
            r_spd, w_spd = 0, 0
            dev_name = mount_map.get(mountpoint)
            if dev_name and dev_name in current_io and dev_name in last_io:
                curr, prev = current_io[dev_name], last_io[dev_name]
                dt = current_time - last_time
                if dt > 0:
                    r_spd = (curr.read_bytes - prev.read_bytes) / dt
                    w_spd = (curr.write_bytes - prev.write_bytes) / dt

            storage_entries.append({
                "name": name, "icon": icon, "total_gb": total_gb, "used_gb": used_gb, "pct": used_pct,
                "temp": temp, "health": health, "lifespan": lifespan, "tbw": tbw,
                "r_spd": r_spd, "w_spd": w_spd
            })
        except Exception: continue

    # ---------------------------------------------------
    # TOOLTIP
    # ---------------------------------------------------
    header_line = (
        f"<span foreground='{SECTION_COLORS['Storage']['icon']}'>{SSD_ICON}</span> "
        f"<span foreground='{SECTION_COLORS['Storage']['text']}'>Storage</span>"
    )
    lines = []

    for entry in storage_entries:
        c_temp = get_color(entry['temp'], "drive_temp") if entry['temp'] else COLORS["bright_black"]
        c_usage = get_color(entry['pct'], "mem_storage")
        health_c = COLORS['green'] if entry['health'] == "OK" else COLORS['red']
        
        # Header: Name
        lines.append(f"<span foreground='{COLORS['white']}'><b>{entry['name']}</b></span>")
        
        # Fixed column width for two-column alignment
        COL1_WIDTH = 20
        
        # Row 1: Size | Temperature
        used_str = f"{entry['used_gb']:>4.0f}GB" if entry['used_gb'] < 1000 else f"{entry['used_gb']/1024:>4.1f}TB"
        total_str = f"{entry['total_gb']:.0f}GB" if entry['total_gb'] < 1000 else f"{entry['total_gb']/1024:.0f}TB"
        temp_str = f"{entry['temp']:>3}°C" if entry['temp'] else " N/A"
        col1 = f"Size: {used_str} / {total_str}"
        col1_pad = ' ' * max(0, COL1_WIDTH - len(col1))
        lines.append(f"󰆼 Size: {span(used_str, c_usage)} / {total_str}{col1_pad} 󰔏 Temp: {span(temp_str, c_temp)}")
        
        # Row 2: Health | Lifespan/TBW
        lifespan_str = entry['lifespan'] if entry['lifespan'] != "N/A" else None
        tbw_str = entry['tbw'] if entry['tbw'] != "N/A" else None
        health_val = entry['health']
        
        if lifespan_str:
            col1 = f"Health: {health_val}"
            col1_pad = ' ' * max(0, COL1_WIDTH - len(col1))
            lines.append(f"󰕥 Health: {span(health_val, health_c)}{col1_pad} 󰣐 Lifespan: {span(lifespan_str, c_usage)}")
        elif tbw_str:
            col1 = f"Health: {health_val}"
            col1_pad = ' ' * max(0, COL1_WIDTH - len(col1))
            lines.append(f"󰕥 Health: {span(health_val, health_c)}{col1_pad} 󰩹 TBW: {span(tbw_str, c_usage)}")
        elif health_val != "N/A":
            lines.append(f"󰕥 Health: {span(health_val, health_c)}")
        
        # Row 3: Read | Write
        rs = format_compact(entry['r_spd'], "/s")
        ws = format_compact(entry['w_spd'], "/s")
        col1 = f"Read: {rs}"
        col1_pad = ' ' * max(0, COL1_WIDTH - len(col1))
        lines.append(f"󰛶 Read: {span(rs, COLORS['blue'])}{col1_pad} 󰛴 Write: {span(ws, COLORS['green'])}")
        
        # Usage Bar - will be sized after calculating tooltip width
        lines.append(f"__BAR_PLACEHOLDER__|{entry['pct']}|{c_usage}")
        lines.append("")

    # Calculate tooltip width based on all content including header
    # Header is rendered at size 14000, body at size 11000, so scale header width accordingly
    body_width = calc_tooltip_width(lines)
    header_width = int(len(strip_pango(header_line)) * 14000 / 11000)
    tooltip_width = max(body_width, header_width)
    
    # Now replace bar placeholders with actual bars
    bar_w = tooltip_width - 6  # Leave room for icon, space, and percentage
    for i, line in enumerate(lines):
        if line.startswith("__BAR_PLACEHOLDER__"):
            _, pct_str, color = line.split("|")
            pct = int(pct_str)
            filled = int((pct / 100) * bar_w)
            bar = f"{span('█'*filled, color)}{span('░'*(bar_w-filled), '#555555')}"
            lines[i] = f"{SSD_ICON} {bar}{span(f'{pct:>3}%', color)}"
    
    # Insert top rule at beginning
    lines.insert(0, f"<span foreground='{COLORS['white']}'>{'─' * tooltip_width}</span>")
    
    lines.append(f"<span foreground='{COLORS['white']}'>{'┈' * tooltip_width}</span>")
    lines.append("󰍽 LMB: File Manager")

    save_history({'io': current_io, 'timestamp': current_time, 'smart': smart_cache})

    print(json.dumps({
        "text": f"{SSD_ICON} {text_span(f'{root_usage}%', get_color(root_usage,'mem_storage'))}",
        "tooltip": f"<span size='14000'>{header_line}</span>\n<span size='11000'>{chr(10).join(lines)}</span>",
        "markup": "pango",
        "class": "storage"
    }))

if __name__ == "__main__":
    main()
