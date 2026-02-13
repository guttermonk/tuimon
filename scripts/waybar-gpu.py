#!/usr/bin/env python3
# ----------------------------------------------------------------------------
# WAYBAR GPU MODULE
# ----------------------------------------------------------------------------
# Visualizes GPU stats including VRAM layout and Die temperature.
# Supports: Nvidia (nvidia-smi), AMD (amdgpu sysfs/rocm-smi), Intel (sysfs)
# ----------------------------------------------------------------------------

import json
import subprocess
import os
import pathlib
import glob
import re
import argparse
import time

# ---------------------------------------------------
# ARGUMENTS
# ---------------------------------------------------
parser = argparse.ArgumentParser(description='Waybar GPU monitor')
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
GPU_ICON = "󰢮"
GPU_ICON_NVIDIA = "󰢮"
GPU_ICON_AMD = "󰢮"
GPU_ICON_INTEL = "󰢮"

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

COLOR_TABLE = [
    {"color": COLORS["blue"],           "cpu_gpu_temp": (0, 35),   "gpu_power": (0.0, 20)},
    {"color": COLORS["cyan"],           "cpu_gpu_temp": (36, 45),  "gpu_power": (21, 40)},
    {"color": COLORS["green"],          "cpu_gpu_temp": (46, 54),  "gpu_power": (41, 60)},
    {"color": COLORS["yellow"],         "cpu_gpu_temp": (55, 65),  "gpu_power": (61, 75)},
    {"color": COLORS["bright_yellow"],  "cpu_gpu_temp": (66, 75),  "gpu_power": (76, 85)},
    {"color": COLORS["bright_red"],     "cpu_gpu_temp": (76, 85),  "gpu_power": (86, 95)},
    {"color": COLORS["red"],            "cpu_gpu_temp": (86, 999), "gpu_power": (96, 999)}
]

def get_color(value, metric_type):
    try: value = float(value)
    except Exception: return "#ffffff"
    for entry in COLOR_TABLE:
        if metric_type in entry:
            low, high = entry[metric_type]
            if low <= value <= high: return entry["color"]
    return COLOR_TABLE[-1]["color"]

# ---------------------------------------------------
# GPU DETECTION
# ---------------------------------------------------
def clean_gpu_name(name):
    """Clean up GPU name by removing redundant suffixes."""
    # Remove common redundant suffixes
    suffixes_to_remove = [
        " Integrated Graphics Controller",
        " Graphics Controller",
        " Integrated Graphics",
        " Graphics",
        " Controller",
    ]
    for suffix in suffixes_to_remove:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name.strip()

def detect_gpu_vendor():
    """Detect which GPU vendor is present. Returns 'nvidia', 'amd', 'intel', or None."""
    # Check for Nvidia
    try:
        subprocess.check_output(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], 
                                stderr=subprocess.DEVNULL)
        return "nvidia"
    except Exception: pass
    
    # Check for AMD (amdgpu driver)
    amd_paths = glob.glob("/sys/class/drm/card*/device/vendor")
    for path in amd_paths:
        try:
            with open(path) as f:
                vendor = f.read().strip()
                if vendor == "0x1002":  # AMD vendor ID
                    return "amd"
        except Exception: pass
    
    # Check for Intel
    intel_paths = glob.glob("/sys/class/drm/card*/device/vendor")
    for path in intel_paths:
        try:
            with open(path) as f:
                vendor = f.read().strip()
                if vendor == "0x8086":  # Intel vendor ID
                    return "intel"
        except Exception: pass
    
    return None

def get_drm_card_path(vendor_id):
    """Find the DRM card path for a specific vendor."""
    for card_path in glob.glob("/sys/class/drm/card[0-9]*"):
        vendor_file = os.path.join(card_path, "device/vendor")
        try:
            with open(vendor_file) as f:
                if f.read().strip() == vendor_id:
                    return card_path
        except Exception: pass
    return None

# ---------------------------------------------------
# NVIDIA GPU
# ---------------------------------------------------
def get_nvidia_stats():
    gpu_percent, gpu_temp, gpu_power, fan_speed = 0, 0, 0.0, 0
    vram_used, vram_total = 0, 0
    gpu_name = "Nvidia GPU"
    gpu_tdp = 250.0
    procs = []

    try:
        # Get Name and Power Limit
        info_cmd = ["nvidia-smi", "--query-gpu=name,power.limit", "--format=csv,noheader,nounits"]
        info_out = subprocess.check_output(info_cmd, text=True, stderr=subprocess.DEVNULL).strip().split(',')
        if len(info_out) >= 2:
            gpu_name = clean_gpu_name(info_out[0].strip())
            try: gpu_tdp = float(info_out[1].strip())
            except Exception: pass

        # Get Stats
        cmd = ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,power.draw,fan.speed,memory.used,memory.total", 
               "--format=csv,noheader,nounits"]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        m = [x.strip() for x in output.split(",")]
        
        gpu_percent = int(m[0])
        gpu_temp = int(m[1])
        gpu_power = float(m[2])
        fan_speed = int(m[3]) if m[3] != '[N/A]' else 0
        vram_used = int(m[4])
        vram_total = int(m[5])

        # Get processes
        try:
            cmd_procs = ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]
            output_procs = subprocess.check_output(cmd_procs, text=True, stderr=subprocess.DEVNULL).strip()
            if output_procs:
                for line in output_procs.split('\n'):
                    parts = [x.strip() for x in line.split(',')]
                    if len(parts) >= 3:
                        name = os.path.basename(parts[1].replace('\\', '/'))
                        try: mem = int(parts[2])
                        except Exception: mem = 0
                        procs.append({'name': name, 'mem': mem})
        except Exception: pass

    except Exception:
        pass

    return {
        "vendor": "nvidia",
        "name": gpu_name,
        "icon": GPU_ICON_NVIDIA,
        "percent": gpu_percent,
        "temp": gpu_temp,
        "power": gpu_power,
        "tdp": gpu_tdp,
        "fan": fan_speed,
        "has_fan": True,  # Nvidia discrete GPUs have fans
        "vram_used": vram_used,
        "vram_total": vram_total,
        "procs": procs
    }

# ---------------------------------------------------
# AMD GPU (amdgpu driver)
# ---------------------------------------------------
def read_sysfs_value(path, default=None):
    """Read a value from sysfs, return default if not available."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default

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

def get_amd_stats():
    gpu_percent, gpu_temp, gpu_power, fan_speed = 0, 0, 0.0, 0
    vram_used, vram_total = 0, 0
    gpu_name = "AMD GPU"
    gpu_tdp = 200.0
    procs = []

    card_path = get_drm_card_path("0x1002")
    if not card_path:
        return None

    device_path = os.path.join(card_path, "device")
    hwmon_paths = glob.glob(os.path.join(device_path, "hwmon/hwmon*"))
    hwmon_path = hwmon_paths[0] if hwmon_paths else None

    # Try to get GPU name
    try:
        # Try from marketing name
        marketing_name = read_sysfs_value(os.path.join(device_path, "product_name"))
        if marketing_name:
            gpu_name = clean_gpu_name(marketing_name)
        else:
            # Fallback to lspci
            try:
                lspci = subprocess.check_output(["lspci"], text=True, stderr=subprocess.DEVNULL)
                for line in lspci.split('\n'):
                    if 'VGA' in line and 'AMD' in line.upper():
                        gpu_name = clean_gpu_name(line.split(':')[-1].strip())
                        break
            except Exception: pass
    except Exception: pass

    # Try rocm-smi first for more accurate data
    try:
        rocm_output = subprocess.check_output(
            ["rocm-smi", "--showuse", "--showtemp", "--showpower", "--showmeminfo", "vram", "--json"],
            text=True, stderr=subprocess.DEVNULL
        )
        data = json.loads(rocm_output)
        # Parse rocm-smi JSON output (format varies by version)
        for card_id, card_data in data.items():
            if isinstance(card_data, dict):
                gpu_percent = int(card_data.get("GPU use (%)", card_data.get("GPU Usage (%)", 0)))
                gpu_temp = int(card_data.get("Temperature (Sensor edge) (C)", 
                              card_data.get("Temperature", 0)))
                power_str = card_data.get("Average Graphics Package Power (W)", "0")
                gpu_power = float(re.sub(r'[^\d.]', '', str(power_str)) or 0)
                vram_used = int(card_data.get("VRAM Total Used Memory (B)", 0)) // (1024*1024)
                vram_total = int(card_data.get("VRAM Total Memory (B)", 0)) // (1024*1024)
                break
    except Exception:
        # Fallback to sysfs
        if hwmon_path:
            # Temperature
            temp_input = read_sysfs_value(os.path.join(hwmon_path, "temp1_input"))
            if temp_input:
                gpu_temp = int(temp_input) // 1000

            # Fan speed (percentage)
            pwm = read_sysfs_value(os.path.join(hwmon_path, "pwm1"))
            if pwm:
                fan_speed = int(int(pwm) / 255 * 100)

            # Power
            power_avg = read_sysfs_value(os.path.join(hwmon_path, "power1_average"))
            if power_avg:
                gpu_power = int(power_avg) / 1_000_000  # microwatts to watts

            # TDP / Power cap
            power_cap = read_sysfs_value(os.path.join(hwmon_path, "power1_cap"))
            if power_cap:
                gpu_tdp = int(power_cap) / 1_000_000

        # GPU utilization from sysfs
        gpu_busy = read_sysfs_value(os.path.join(device_path, "gpu_busy_percent"))
        if gpu_busy:
            gpu_percent = int(gpu_busy)

        # VRAM from sysfs
        vram_used_bytes = read_sysfs_value(os.path.join(device_path, "mem_info_vram_used"))
        vram_total_bytes = read_sysfs_value(os.path.join(device_path, "mem_info_vram_total"))
        if vram_used_bytes:
            vram_used = int(vram_used_bytes) // (1024*1024)
        if vram_total_bytes:
            vram_total = int(vram_total_bytes) // (1024*1024)

    # Try to get processes using GPU (via /sys/kernel/debug/dri or fdinfo)
    try:
        # This requires appropriate permissions
        fdinfo_path = os.path.join(card_path.replace("/sys/class/drm", "/sys/kernel/debug/dri"), "clients")
        if os.path.exists(fdinfo_path):
            with open(fdinfo_path) as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0].isdigit():
                        try:
                            pid = int(parts[0])
                            cmdline_path = f"/proc/{pid}/comm"
                            with open(cmdline_path) as cf:
                                name = cf.read().strip()
                            procs.append({'name': name, 'mem': 0})
                        except Exception: pass
    except Exception: pass

    # Detect if this is an integrated AMD GPU (APU) - they typically have no dedicated VRAM
    is_integrated = vram_total == 0
    return {
        "vendor": "amd",
        "name": gpu_name,
        "icon": GPU_ICON_AMD,
        "percent": gpu_percent,
        "temp": gpu_temp,
        "power": gpu_power,
        "tdp": gpu_tdp,
        "fan": fan_speed,
        "has_fan": not is_integrated and fan_speed > 0,  # Discrete AMD GPUs have fans, APUs don't
        "vram_used": vram_used,
        "vram_total": vram_total,
        "procs": procs
    }

# ---------------------------------------------------
# INTEL GPU
# ---------------------------------------------------
def get_intel_stats():
    gpu_percent, gpu_temp, gpu_power, fan_speed = 0, 0, 0.0, 0
    vram_used, vram_total = 0, 0  # Intel uses shared memory
    gpu_name = "Intel GPU"
    gpu_tdp = 15.0  # Typical for integrated
    procs = []

    card_path = get_drm_card_path("0x8086")
    if not card_path:
        return None

    device_path = os.path.join(card_path, "device")
    card_name = os.path.basename(card_path)  # e.g., "card0"

    # Try to get GPU name from lspci
    try:
        lspci = subprocess.check_output(["lspci", "-nn"], text=True, stderr=subprocess.DEVNULL)
        for line in lspci.split('\n'):
            if 'VGA' in line and 'Intel' in line:
                # Extract the descriptive name
                match = re.search(r'Intel Corporation (.+?) \[', line)
                if match:
                    gpu_name = clean_gpu_name(f"Intel {match.group(1).strip()}")
                else:
                    # Try alternate pattern
                    match2 = re.search(r'Intel Corporation (.+?)$', line)
                    if match2:
                        gpu_name = clean_gpu_name(f"Intel {match2.group(1).strip()}")
                    else:
                        gpu_name = "Intel Integrated"
                break
    except Exception: pass

    # Try intel_gpu_top for utilization (requires intel-gpu-tools)
    # May need sudo for perf access
    # Only try without sudo - sudo would block waiting for password in non-interactive waybar context
    for cmd in [["intel_gpu_top", "-J", "-s", "500"]]:
        if gpu_percent > 0:
            break
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
            try:
                output, _ = proc.communicate(timeout=1.5)
                # intel_gpu_top outputs multiple JSON objects, take the last complete one
                lines = output.strip().split('\n')
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            data = json.loads(line)
                            if "engines" in data:
                                for engine_name, engine in data["engines"].items():
                                    if isinstance(engine, dict) and "busy" in engine:
                                        gpu_percent = max(gpu_percent, int(float(engine["busy"])))
                            break
                        except json.JSONDecodeError:
                            continue
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
        except FileNotFoundError:
            break  # intel_gpu_top not installed
        except Exception: pass

    # Fallback: Try reading from sysfs frequency info
    if gpu_percent == 0:
        # Check for i915 driver sysfs entries - multiple possible paths
        # Use card_path directly since it's already the full path
        freq_paths = [
            os.path.join(card_path, "gt_act_freq_mhz"),
            os.path.join(card_path, "gt_cur_freq_mhz"),
            os.path.join(card_path, "gt/gt0/rps_cur_freq_mhz"),
        ]
        max_freq_paths = [
            os.path.join(card_path, "gt_max_freq_mhz"),
            os.path.join(card_path, "gt_RP0_freq_mhz"),
            os.path.join(card_path, "gt/gt0/rps_max_freq_mhz"),
        ]
        
        act_freq = None
        max_freq = None
        
        for path in freq_paths:
            val = read_sysfs_value(path)
            if val:
                try:
                    act_freq = int(val)
                    break
                except Exception: pass
        
        for path in max_freq_paths:
            val = read_sysfs_value(path)
            if val:
                try:
                    max_freq = int(val)
                    break
                except Exception: pass
        
        if act_freq and max_freq and max_freq > 0:
            gpu_percent = min(100, int((act_freq / max_freq) * 100))

    # Temperature - check multiple sources
    # 1. Try i915 hwmon directly
    hwmon_paths = glob.glob(os.path.join(card_path, "device/hwmon/hwmon*"))
    for hwmon in hwmon_paths:
        temp = read_sysfs_value(os.path.join(hwmon, "temp1_input"))
        if temp:
            try:
                gpu_temp = int(temp) // 1000
                break
            except Exception: pass
    
    # 2. Try sensors command for i915 or pch_* (PCH often reports GPU-related temps)
    if gpu_temp == 0:
        try:
            output = subprocess.check_output(["sensors", "-j"], text=True, stderr=subprocess.DEVNULL)
            data = json.loads(output)
            for chip, content in data.items():
                chip_lower = chip.lower()
                if "i915" in chip_lower or "pch" in chip_lower:
                    if isinstance(content, dict):
                        for feature, values in content.items():
                            if isinstance(values, dict):
                                for key, val in values.items():
                                    if "input" in key and isinstance(val, (int, float)):
                                        gpu_temp = int(val)
                                        break
                                if gpu_temp > 0:
                                    break
                    if gpu_temp > 0:
                        break
        except Exception: pass

    # 3. For integrated GPUs, fall back to x86_pkg_temp (CPU package temp includes iGPU)
    if gpu_temp == 0:
        for tz_path in glob.glob("/sys/class/thermal/thermal_zone*"):
            tz_type = read_sysfs_value(os.path.join(tz_path, "type"))
            if tz_type and "x86_pkg_temp" in tz_type:
                temp = read_sysfs_value(os.path.join(tz_path, "temp"))
                if temp:
                    try:
                        gpu_temp = int(temp) // 1000
                        break
                    except Exception: pass
    
    # 4. Fall back to coretemp hwmon (Package id 0)
    if gpu_temp == 0:
        for hwmon_path in glob.glob("/sys/class/hwmon/hwmon*"):
            name = read_sysfs_value(os.path.join(hwmon_path, "name"))
            if name and "coretemp" in name:
                # Look for Package id 0 temperature
                for i in range(1, 10):
                    label = read_sysfs_value(os.path.join(hwmon_path, f"temp{i}_label"))
                    if label and "Package" in label:
                        temp = read_sysfs_value(os.path.join(hwmon_path, f"temp{i}_input"))
                        if temp:
                            try:
                                gpu_temp = int(temp) // 1000
                                break
                            except Exception: pass
                if gpu_temp > 0:
                    break

    # For Intel, VRAM is shared system memory - try to get usage
    # Check debugfs or sysfs for memory info
    card_num = card_name.replace('card', '')
    mem_paths = [
        f"/sys/kernel/debug/dri/{card_num}/i915_gem_objects",
        os.path.join(card_path, "device/resource0_resize"),
    ]
    for path in mem_paths:
        content = read_sysfs_value(path)
        if content:
            match = re.search(r'(\d+)\s*bytes', content)
            if match:
                vram_used = int(match.group(1)) // (1024*1024)
                break
            # Also try to find total/used patterns
            match = re.search(r'total:\s*(\d+)', content)
            if match:
                vram_total = int(match.group(1)) // (1024*1024)
            match = re.search(r'used:\s*(\d+)', content)
            if match:
                vram_used = int(match.group(1)) // (1024*1024)
                break

    # Power reading - try RAPL for GPU domain or hwmon
    # Intel GPU power is often in intel-rapl:0:1 (uncore) or similar
    rapl_paths = glob.glob("/sys/class/powercap/intel-rapl:*/intel-rapl:*:*/energy_uj")
    for rapl_path in rapl_paths:
        name_path = os.path.join(os.path.dirname(rapl_path), "name")
        name = read_sysfs_value(name_path)
        if name and "uncore" in name.lower():
            try:
                energy1 = read_rapl_energy(rapl_path)
                if energy1 is not None:
                    time.sleep(0.05)
                    energy2 = read_rapl_energy(rapl_path)
                    if energy2 is not None and energy2 > energy1:
                        gpu_power = (energy2 - energy1) / 1_000_000 / 0.05
                break
            except Exception: pass
    
    # Fallback to hwmon power
    if gpu_power == 0:
        for hwmon in hwmon_paths:
            power_avg = read_sysfs_value(os.path.join(hwmon, "power1_average"))
            if power_avg:
                try:
                    gpu_power = int(power_avg) / 1_000_000
                    break
                except Exception: pass

    # Process detection for Intel GPU
    # Check /proc/*/fdinfo/* for i915 DRM clients
    seen_pids = set()
    try:
        for proc_dir in glob.glob("/proc/[0-9]*"):
            try:
                pid = os.path.basename(proc_dir)
                if pid in seen_pids:
                    continue
                    
                fdinfo_dir = os.path.join(proc_dir, "fdinfo")
                if not os.path.isdir(fdinfo_dir):
                    continue
                
                uses_gpu = False
                gpu_mem = 0
                
                for fd_file in os.listdir(fdinfo_dir):
                    try:
                        fdinfo_path = os.path.join(fdinfo_dir, fd_file)
                        content = read_sysfs_value(fdinfo_path)
                        if content and "drm-driver:\ti915" in content:
                            uses_gpu = True
                            # Try to get memory usage from various fields
                            for line in content.split('\n'):
                                # Check for engine render time (newer kernels)
                                if line.startswith("drm-engine-render:"):
                                    try:
                                        ns = int(line.split(':')[1].strip().split()[0])
                                        gpu_mem = max(gpu_mem, ns // 1_000_000)
                                    except Exception: pass
                                # Check for active/resident memory
                                elif "drm-active-" in line or "drm-resident-" in line:
                                    try:
                                        val = int(line.split(':')[1].strip().split()[0])
                                        if val > 0:
                                            gpu_mem = max(gpu_mem, val // 1024)  # Convert to KB
                                    except Exception: pass
                            break  # Found i915, no need to check more fds
                    except Exception: continue
                
                if uses_gpu:
                    seen_pids.add(pid)
                    comm_path = os.path.join(proc_dir, "comm")
                    name = read_sysfs_value(comm_path) or f"pid:{pid}"
                    procs.append({'name': name, 'mem': gpu_mem})
            except Exception: continue
    except Exception: pass

    return {
        "vendor": "intel",
        "name": gpu_name,
        "icon": GPU_ICON_INTEL,
        "percent": gpu_percent,
        "temp": gpu_temp,
        "power": gpu_power,
        "tdp": gpu_tdp,
        "fan": fan_speed,
        "has_fan": False,  # Intel integrated GPUs don't have dedicated fans
        "vram_used": vram_used,
        "vram_total": vram_total,
        "procs": procs
    }

# ---------------------------------------------------
# GRAPHIC GENERATOR
# ---------------------------------------------------
def generate_gpu_graphic(stats):
    gpu_percent = stats["percent"]
    gpu_temp = stats["temp"]
    gpu_power = stats["power"]
    gpu_tdp = stats["tdp"]
    fan_speed = stats["fan"]
    has_fan = stats.get("has_fan", fan_speed > 0)
    vram_pct = (stats["vram_used"] / stats["vram_total"] * 100) if stats["vram_total"] > 0 else 0
    pwr_pct = (gpu_power / gpu_tdp * 100) if gpu_tdp > 0 else 0

    def get_vram_color(usage, level):
        if usage > (level - 1) * (100 / 6):
            return get_color(usage, 'gpu_power')
        return COLORS["white"]

    def get_bar_segment(val, threshold):
        char_map = {80: "███", 60: "▅▅▅", 40: "▃▃▃", 20: "▂▂▂", 0: "───"}
        color = get_color(val, 'gpu_power') if val > threshold else "#555555"
        return f"<span foreground='{color}'>{char_map[threshold]}</span>"

    die_temp_color = get_color(gpu_temp, 'cpu_gpu_temp')
    f_c = COLORS["white"]
    def bg(t):
        return f"<span foreground='{die_temp_color}'>{t}</span>"

    # VRAM Chips
    vc = [get_vram_color(vram_pct, i) for i in range(1, 7)]

    # Internal bars - adaptive based on fan presence
    bars = []
    for thresh in [80, 60, 40, 20, 0]:
        if has_fan:
            bars.append(f"{get_bar_segment(gpu_percent, thresh)} {get_bar_segment(pwr_pct, thresh)} {get_bar_segment(fan_speed, thresh)}")
        else:
            bars.append(f"{get_bar_segment(gpu_percent, thresh)} {get_bar_segment(pwr_pct, thresh)}")

    if has_fan:
        # Full graphic with fan column
        graphic = [
            f"      <span foreground='{f_c}'>╭─────────────────╮</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[5]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░░░░░░░░░░░░░░░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[5]}'>███</span><span foreground='{f_c}'>=</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[4]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')}  󰓅   󰚥   󰈐  {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[4]}'>███</span><span foreground='{f_c}'>=</span>",
            f"      <span foreground='{f_c}'>│</span>{bg('░░')} {bars[0]} {bg('░░')}<span foreground='{f_c}'>│</span>  ",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[3]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')} {bars[1]} {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[3]}'>███</span><span foreground='{f_c}'>=</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[2]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')} {bars[2]} {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[2]}'>███</span><span foreground='{f_c}'>=</span>",
            f"      <span foreground='{f_c}'>│</span>{bg('░░')} {bars[3]} {bg('░░')}<span foreground='{f_c}'>│</span>  ",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[1]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')} {bars[4]} {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[1]}'>███</span><span foreground='{f_c}'>=</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[0]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░░░░░░░░░░░░░░░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[0]}'>███</span><span foreground='{f_c}'>=</span>",
            f"      <span foreground='{f_c}'>╰─────────────────╯</span>"
        ]
    else:
        # Compact graphic without fan column
        graphic = [
            f"      <span foreground='{f_c}'>╭─────────────╮</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[5]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░░░░░░░░░░░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[5]}'>███</span><span foreground='{f_c}'>=</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[4]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')}  󰓅   󰚥  {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[4]}'>███</span><span foreground='{f_c}'>=</span>",
            f"      <span foreground='{f_c}'>│</span>{bg('░░')} {bars[0]} {bg('░░')}<span foreground='{f_c}'>│</span>  ",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[3]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')} {bars[1]} {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[3]}'>███</span><span foreground='{f_c}'>=</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[2]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')} {bars[2]} {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[2]}'>███</span><span foreground='{f_c}'>=</span>",
            f"      <span foreground='{f_c}'>│</span>{bg('░░')} {bars[3]} {bg('░░')}<span foreground='{f_c}'>│</span>  ",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[1]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░')} {bars[4]} {bg('░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[1]}'>███</span><span foreground='{f_c}'>=</span>",
            f" <span foreground='{f_c}'>=</span><span foreground='{vc[0]}'>███</span><span foreground='{f_c}'>=│</span>{bg('░░░░░░░░░░░░░')}<span foreground='{f_c}'>│=</span><span foreground='{vc[0]}'>███</span><span foreground='{f_c}'>=</span>",
            f"      <span foreground='{f_c}'>╰─────────────╯</span>"
        ]
    
    return graphic

# ---------------------------------------------------
# MAIN
# ---------------------------------------------------
def main():
    # Detect and get stats for available GPU
    vendor = detect_gpu_vendor()
    
    if vendor == "nvidia":
        stats = get_nvidia_stats()
    elif vendor == "amd":
        stats = get_amd_stats()
    elif vendor == "intel":
        stats = get_intel_stats()
    else:
        # No GPU detected
        print(json.dumps({
            "text": f"{GPU_ICON} N/A",
            "tooltip": "No supported GPU detected",
            "class": "gpu"
        }))
        return

    if not stats:
        print(json.dumps({
            "text": f"{GPU_ICON} N/A",
            "tooltip": f"Could not read {vendor} GPU stats",
            "class": "gpu"
        }))
        return

    gpu_temp = stats["temp"]
    gpu_percent = stats["percent"]
    gpu_power = stats["power"]
    gpu_tdp = stats["tdp"]
    fan_speed = stats["fan"]
    vram_used = stats["vram_used"]
    vram_total = stats["vram_total"]
    vram_pct = (vram_used / vram_total * 100) if vram_total > 0 else 0
    pwr_pct = (gpu_power / gpu_tdp * 100) if gpu_tdp > 0 else 0

    die_temp_color = get_color(gpu_temp, 'cpu_gpu_temp')
    
    # Vendor-specific color
    # Use header color override if set, otherwise fall back to vendor-specific colors
    if "gpu" in HEADER_COLORS:
        vendor_color = HEADER_COLORS["gpu"]
    else:
        vendor_colors = {
            "nvidia": COLORS["green"],
            "amd": COLORS["red"],
            "intel": COLORS["blue"]
        }
        vendor_color = vendor_colors.get(stats["vendor"], COLORS["yellow"])

    # Generate graphic
    graphic = generate_gpu_graphic(stats)

    # Build tooltip
    header_line = f"<span foreground='{vendor_color}'>{stats['icon']}</span> <span foreground='{vendor_color}'>GPU</span> - {stats['name']}"
    tooltip_lines = []
    tooltip_lines.extend([
        f"󰔏 Temperature: {span(f'{gpu_temp:>3}°C', die_temp_color)}",
    ])

    # VRAM line (different for Intel shared memory)
    if stats["vendor"] == "intel" and vram_total == 0:
        tooltip_lines.append(f"󰘚 Memory: {span('Shared System RAM', COLORS['cyan'])}")
    elif vram_total > 0:
        tooltip_lines.append(f"󰘚 V-RAM: {span(f'{vram_used:>5}/{vram_total:<5}MB', get_color(vram_pct, 'gpu_power'))}")
    
    tooltip_lines.extend([
        f"󰚥 Power: {span(f'{gpu_power:>6.1f}W', get_color(pwr_pct, 'gpu_power'))}",
        f"󰓅 Utilization: {span(f'{gpu_percent:>3}%', get_color(gpu_percent, 'gpu_power'))}",
    ])

    # Fan speed (only show if GPU has a fan)
    has_fan = stats.get("has_fan", fan_speed > 0 or stats["vendor"] == "nvidia")
    if has_fan:
        tooltip_lines.append(f"󰈐 Fan Speed: {span(f'{fan_speed:>3}%', get_color(fan_speed, 'gpu_power'))}")

    # Build process lines for width calculation (before adding graphic)
    process_lines = []
    process_lines.append("Top GPU Processes:")
    if stats["procs"]:
        procs = sorted(stats["procs"], key=lambda x: x.get('mem', 0), reverse=True)
        for p in procs[:5]:
            name = p['name']
            if len(name) > 12: name = name[:11] + "…"
            mem = p.get('mem', 0)
            if mem > 0 and vram_total > 0:
                mem_p = (mem / vram_total * 100)
                color = get_color(mem_p, 'gpu_power')
                process_lines.append(f" • {name:<12} {span(f'󰘚 {mem_p:>5.1f}% ({mem}MB)', color)}")
            else:
                process_lines.append(f" • {name:<12}")
    else:
        process_lines.append(" • No GPU processes detected")

    # Calculate tooltip width BEFORE adding graphics (based on non-graphic content)
    # Header is rendered at size 14000, body at size 11000, so scale header width accordingly
    all_non_graphic = tooltip_lines + process_lines
    body_width = calc_tooltip_width(all_non_graphic)
    header_width = int(len(strip_pango(header_line)) * 14000 / 11000)
    tooltip_width = max(body_width, header_width)

    # Calculate graphic width and centering padding
    # Graphic width: 30 chars with fan, 26 chars without fan (widest line plain text width)
    graphic_width = 30 if has_fan else 26
    center_padding = " " * max(0, (tooltip_width - graphic_width) // 2)

    # Apply centering to graphic lines
    centered_graphic = [f"{center_padding}{line}" for line in graphic]

    tooltip_lines.extend([
        "",
        "\n".join(centered_graphic),
        ""
    ])

    # Add process lines
    tooltip_lines.extend(process_lines)
    
    # Insert top rule at beginning
    tooltip_lines.insert(0, "─" * tooltip_width)
    
    tooltip_lines.extend([
        "",
        f"<span foreground='{COLORS['white']}'>{'┈' * tooltip_width}</span>",
        "󰍽 LMB: Btop"
    ])

    # Handle click events
    click_type = os.environ.get("WAYBAR_CLICK_TYPE")
    if click_type == "right":
        # Could open vendor-specific control panel
        pass 

    # Build display text based on --display argument
    percent_color = get_color(gpu_percent, 'gpu_power')
    
    icon_text = f"<span size='21000' rise='-3000'>{stats['icon']}</span>" if not args.plain else stats['icon']
    
    if args.display == "temp":
        display_text = f"{icon_text} {text_span(f'{gpu_temp}°C', die_temp_color)}"
    elif args.display == "percent":
        display_text = f"{icon_text} {text_span(f'{gpu_percent}%', percent_color)}"
    else:  # both
        display_text = f"{icon_text} {text_span(f'{gpu_temp}°C', die_temp_color)} {text_span(f'{gpu_percent}%', percent_color)}"

    print(json.dumps({
        "text": display_text,
        "tooltip": f"<span size='14000'>{header_line}</span>\n<span size='11000'>{chr(10).join(tooltip_lines)}</span>",
        "markup": "pango",
        "class": f"gpu gpu-{stats['vendor']}"
    }))

if __name__ == "__main__":
    main()