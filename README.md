# Waybar System Monitors

System monitoring scripts for Waybar with rich, graphical tooltips. Based on scripts by [Balthazzahr](https://gist.github.com/Balthazzahr).

## Features

- **CPU Monitor**: Per-core usage visualization, power usage (RAPL), temperature, top processes
- **Memory Monitor**: RAM usage breakdown (Used, Cached, Buffers), DIMM detection via dmidecode, temperature
- **GPU Monitor**: Multi-vendor support (Nvidia, AMD, Intel), VRAM usage, power, fan speed, graphical die visualization
- **Storage Monitor**: Auto-detects mounted drives, I/O speeds, temperature, SMART health status

## Dependencies

### Required

| Package | NixOS | Debian/Ubuntu | Purpose |
|---------|-------|---------------|---------|
| Python 3 | `python3` | `python3` | Runtime |
| psutil | `python3Packages.psutil` | `python3-psutil` | System metrics |
| lm_sensors | `lm_sensors` | `lm-sensors` | Temperature readings |
| procps | `procps` | `procps` | Process listing (ps) |
| pciutils | `pciutils` | `pciutils` | GPU detection (lspci) |

### Optional

| Package | NixOS | Debian/Ubuntu | Purpose |
|---------|-------|---------------|---------|
| dmidecode | `dmidecode` | `dmidecode` | RAM module detection |
| smartmontools | `smartmontools` | `smartmontools` | Storage health/SMART |
| rocm-smi | `rocmPackages.rocm-smi` | `rocm-smi` | AMD GPU stats |
| intel-gpu-tools | `intel-gpu-tools` | `intel-gpu-tools` | Intel GPU stats |
| Nerd Font | `nerdfonts` | - | Icons |

### NixOS

When using the flake, all required dependencies are automatically included. Optional tools are installed based on your configuration (e.g., `gpu = "amd"` installs `rocm-smi`).

### Other Distros

```bash
# Debian/Ubuntu
sudo apt install python3 python3-psutil lm-sensors procps pciutils dmidecode smartmontools

# Arch
sudo pacman -S python python-psutil lm_sensors procps-ng pciutils dmidecode smartmontools

# Fedora
sudo dnf install python3 python3-psutil lm_sensors procps-ng pciutils dmidecode smartmontools
```

## Installation

### Using Flakes (Recommended)

Add to your `flake.nix`:

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager.url = "github:nix-community/home-manager";
    waybar-system-monitors.url = "path:/path/to/tuimon"; # or your git URL
  };

  outputs = { self, nixpkgs, home-manager, waybar-system-monitors, ... }: {
    # For home-manager standalone
    homeConfigurations."youruser" = home-manager.lib.homeManagerConfiguration {
      # ...
      modules = [
        waybar-system-monitors.homeManagerModules.default
        # your other modules
      ];
    };

    # For NixOS with home-manager module
    nixosConfigurations.yourhostname = nixpkgs.lib.nixosSystem {
      # ...
      modules = [
        waybar-system-monitors.nixosModules.default
        home-manager.nixosModules.home-manager
        {
          home-manager.sharedModules = [
            waybar-system-monitors.homeManagerModules.default
          ];
        }
      ];
    };
  };
}
```

### Manual Installation

You can also install the package directly:

```nix
{ pkgs, ... }:
let
  waybar-system-monitors = pkgs.callPackage /path/to/tuimon/default.nix { };
in
{
  home.packages = [ waybar-system-monitors ];
}
```

## Configuration

### Home-Manager Module

```nix
{ config, ... }:
{
  programs.waybar-system-monitors = {
    enable = true;
    
    # Enable/disable specific monitors
    enableCpu = true;
    enableMemory = true;
    enableGpu = true;
    enableStorage = true;
    
    # Display mode for CPU module: "temp", "percent", or "both"
    cpuDisplay = "both";
    
    # Display mode for GPU module: "temp", "percent", or "both"
    gpuDisplay = "temp";
    
    # GPU vendor (installs appropriate tools)
    # Options: "nvidia", "amd", "intel", or null for auto-detect
    gpu = "amd";
    
    # Terminal to use when clicking modules (for btop)
    terminal = "alacritty";
    
    # true - Use plain text output (no Pango colors) for CSS styling
    # false - Use inline Pango `foreground` colors
    plain = true;
    
    # File manager for storage module click
    # Use "xdg-open" for GUI file managers, or specify a TUI file manager
    # TUI file managers (yazi, ranger, lf, nnn, mc, vifm) are automatically wrapped in terminal
    fileManager = "yazi";
    
    # Global update interval in seconds (applies to modules without a
    # per-module interval). If unset, each module uses its own default:
    # cpu 2, gpu 2, memory 3, storage 60.
    interval = 2;

    # Per-module intervals (seconds, or "once" for a single run).
    # These take precedence over the global `interval`.
    # cpuInterval = 2;
    # gpuInterval = 2;
    # memoryInterval = 3;      # live RAM usage + DIMM temps; the static DIMM
    #                          # inventory (dmidecode) is cached per boot anyway
    # storageInterval = 60;    # usage/IO/temps; SMART health data is cached
    #                          # for an hour regardless of this setting
    
    # Custom colors (Catppuccin-inspired defaults)
    colors = {
      colors = {
        normal = {
          black = "#303446";
          red = "#e78284";
          green = "#a6d189";
          yellow = "#e5c890";
          blue = "#8caaee";
          magenta = "#ca9ee6";
          cyan = "#81c8be";
          white = "#c6d0f5";
        };
        bright = {
          black = "#626880";
          red = "#e78284";
          green = "#a6d189";
          yellow = "#e5c890";
          blue = "#8caaee";
          magenta = "#ca9ee6";
          cyan = "#81c8be";
          white = "#a5adce";
        };
      };
    };
  };
}
```

### Waybar Integration

After enabling the module, add the generated modules to your waybar config:

```nix
{ config, ... }:
{
  programs.waybar = {
    enable = true;
    settings = {
      mainBar = {
        layer = "top";
        modules-right = [
          "custom/cpu"
          "custom/memory"
          "custom/gpu"
          "custom/storage"
        ];
        
        # Merge in the generated module configs
      } // config.programs.waybar-system-monitors.waybarConfig;
    };
  };
}
```

### Manual Waybar Configuration

If not using the home-manager module, add these to your waybar config:

```json
{
  "custom/cpu": {
    "exec": "waybar-cpu",
    "return-type": "json",
    "interval": 2,
    "tooltip": true,
    "on-click": "alacritty -e btop"
  },
  "custom/memory": {
    "exec": "waybar-memory",
    "return-type": "json",
    "interval": 3,
    "tooltip": true
  },
  "custom/gpu": {
    "exec": "waybar-gpu",
    "return-type": "json",
    "interval": 2,
    "tooltip": true,
    "on-click": "alacritty -e btop"
  },
  "custom/storage": {
    "exec": "waybar-storage",
    "return-type": "json",
    "interval": 60,
    "tooltip": true,
    "on-click": "xdg-open ~"
  }
}
```

Storage is fine at a long interval: disk usage and SMART data change slowly, and
the heavy `smartctl -a` health read is cached internally for an hour either way.
Memory can also be set high (or to `"once"`), but note that the live RAM usage
percentage and DIMM temperatures only refresh on this interval — the static DIMM
inventory from `dmidecode` is cached per boot regardless.

## Styling

By default, the modules use inline Pango markup for colors. If you prefer to use your own CSS colors, enable plain mode:

```nix
programs.waybar-system-monitors = {
  enable = true;
  plain = true;  # Disable inline colors
};
```

Then style with CSS in your waybar config:

```css
#custom-cpu {
  color: #e78284;
}

#custom-memory {
  color: #a6d189;
}

#custom-gpu {
  color: #e5c890;
}

#custom-storage {
  color: #8caaee;
}
```

## Requirements

### CPU Monitor
- `lm_sensors` - for temperature readings
- Access to `/sys/class/powercap` for RAPL power readings (usually available by default)

### Memory Monitor
- `lm_sensors` - for DIMM temperature readings (requires jc42/spd sensors)
- `dmidecode` - for RAM module detection (requires sudo, see below)

### GPU Monitor

The GPU monitor auto-detects your GPU vendor and uses the appropriate method.

Set `gpu = "vendor"` in your config to install additional tools:

| Vendor | Config | Extra Tools Installed |
|--------|--------|----------------------|
| Nvidia | `gpu = "nvidia"` | None (uses `nvidia-smi` from drivers) |
| AMD | `gpu = "amd"` | `rocm-smi` for better accuracy |
| Intel | `gpu = "intel"` | `intel-gpu-tools` for utilization stats |
| Auto | `gpu = null` | None (basic sysfs readings only) |

**Nvidia GPUs:**
- Requires Nvidia drivers (`nvidia-x11` or similar)

**AMD GPUs:**
- Works out of the box with `amdgpu` driver (reads from sysfs)
- With `gpu = "amd"`: installs `rocm-smi` for more accurate readings

**Intel GPUs:**
- Works with integrated graphics using `i915` driver
- With `gpu = "intel"`: installs `intel-gpu-tools` for utilization stats

### Storage Monitor
- `lm_sensors` - for NVMe temperature readings
- `smartmontools` - for SMART health data (requires sudo, see below)

## Permissions & Sudo Configuration

Some advanced features require elevated permissions. The scripts are designed to **gracefully degrade** - if sudo isn't configured, the feature simply won't display (no errors or hangs).

### Features Requiring Sudo

| Feature | Command | What You Get | Without Sudo |
|---------|---------|--------------|--------------|
| RAM module details | `dmidecode` | DIMM slots, sizes, speeds, types | Basic usage stats only |
| Storage SMART health | `smartctl` | Health status, lifespan %, TBW | Temperature only (via hwmon) |

### Features That Work Without Sudo

| Feature | Source | Notes |
|---------|--------|-------|
| CPU temperature | `lm_sensors` / sysfs | Works out of the box |
| CPU power (RAPL) | `/sys/class/powercap` | Usually readable by default |
| Memory temperature | `lm_sensors` | Requires jc42/spd sensors or applesmc (MacBooks) |
| GPU stats | sysfs / vendor tools | Nvidia uses `nvidia-smi`, AMD/Intel use sysfs |
| Storage temperature | hwmon / `lm_sensors` | NVMe drives expose temp via hwmon |
| Disk I/O speeds | `psutil` | Works out of the box |

### Configuring Sudo

#### NixOS (Automatic)

The NixOS module configures sudo rules automatically when enabled:

```nix
programs.waybar-system-monitors.enable = true;
```

#### NixOS (Manual)

```nix
# In your NixOS configuration
security.sudo.extraRules = [
  {
    groups = [ "wheel" ];
    commands = [
      { command = "/run/current-system/sw/bin/dmidecode"; options = [ "NOPASSWD" ]; }
      { command = "/run/current-system/sw/bin/smartctl"; options = [ "NOPASSWD" ]; }
    ];
  }
];
```

#### Other Distros

Add to `/etc/sudoers` (use `visudo`):
```
%wheel ALL=(root) NOPASSWD: /usr/sbin/dmidecode
%wheel ALL=(root) NOPASSWD: /usr/sbin/smartctl
```

Or create `/etc/sudoers.d/waybar-monitors`:
```
%wheel ALL=(root) NOPASSWD: /usr/sbin/dmidecode
%wheel ALL=(root) NOPASSWD: /usr/sbin/smartctl
```

## Color Theme

The scripts look for a color theme file at `~/.config/waybar/colors.toml`. The home-manager module generates this automatically from the `colors` option.

Example `colors.toml`:

```toml
[colors.normal]
black = "#303446"
red = "#e78284"
green = "#a6d189"
yellow = "#e5c890"
blue = "#8caaee"
magenta = "#ca9ee6"
cyan = "#81c8be"
white = "#c6d0f5"

[colors.bright]
black = "#626880"
red = "#e78284"
green = "#a6d189"
yellow = "#e5c890"
blue = "#8caaee"
magenta = "#ca9ee6"
cyan = "#81c8be"
white = "#a5adce"
```

## Fonts

The tooltips use Nerd Font icons. Make sure you have a Nerd Font installed, such as:

```nix
fonts.packages = with pkgs; [
  (nerdfonts.override { fonts = [ "JetBrainsMono" ]; })
];
```

## Troubleshooting

### No temperature readings
- Run `sensors-detect` as root to detect sensors
- Ensure `lm_sensors` kernel modules are loaded
- Check available sensors with `sensors` command

### Memory temperature not showing
- Standard DIMMs: Requires jc42/spd sensors (check `sensors | grep -i jc42`)
- MacBooks: Uses applesmc sensors (TM0P, Tm0P) automatically
- Some laptops with soldered RAM may not expose memory temperature

### Memory module info missing
- Ensure `dmidecode` is installed and sudo is configured (see [Permissions](#permissions--sudo-configuration))
- Test with `sudo dmidecode --type memory`
- Without sudo, you'll still see usage stats and temperature (if available)

### GPU module not working

**Nvidia:**
- Ensure Nvidia drivers are installed
- Test with `nvidia-smi` command

**AMD:**
- Ensure `amdgpu` driver is loaded
- Check `/sys/class/drm/card*/device/vendor` contains `0x1002`
- For more stats, install `rocm-smi`

**Intel:**
- Ensure `i915` driver is loaded
- Check `/sys/class/drm/card*/device/vendor` contains `0x8086`
- For utilization stats, install `intel-gpu-tools` and run `intel_gpu_top`

### Storage health/temperature missing

**Temperature not showing:**
- NVMe drives: Should work automatically via hwmon (`/sys/class/hwmon/*/name` = "nvme")
- SATA drives: May require `hddtemp` or `smartctl` with sudo
- Check with `sensors | grep -i nvme` or `cat /sys/class/hwmon/hwmon*/temp1_input`

**Health/Lifespan not showing:**
- Install `smartmontools`
- Configure sudo for `smartctl` (see [Permissions](#permissions--sudo-configuration))
- Test with `sudo smartctl -a /dev/nvme0n1` (or your device)
- LUKS/LVM: The script traces through to the underlying physical device automatically

## FAQ

### Does this use a lot of power/CPU?

**No.** These scripts use Waybar's polling model and are very lightweight:

- Scripts run **briefly on each interval** (2s for cpu/gpu, 3s for memory, 60s for storage by default) then fully exit
- Each run takes ~0.2-0.3 seconds, meaning scripts are idle ~98% of the time
- Hovering over modules to view tooltips **does not increase CPU usage** — tooltip content is generated in the same run as the bar text and cached by Waybar
- No persistent background processes between intervals
- Expensive hardware queries are cached: the memory module's `dmidecode` DIMM scan runs once per boot, and the storage module's `smartctl -a` health read runs at most once per hour

Power consumption is comparable to running `htop` with a 2-second refresh, but lighter since processes fully terminate between intervals. To reduce power further, increase the intervals via the global `interval` option or the per-module `cpuInterval`, `gpuInterval`, `memoryInterval`, and `storageInterval` options (each also accepts `"once"` for a single run).

## Credits

Original scripts by [Balthazzahr](https://gist.github.com/Balthazzahr):
- [waybar-cpu.py](https://gist.github.com/Balthazzahr/bae4df460811fc3ebb5ab29141ecf936)
- [waybar-memory.py](https://gist.github.com/Balthazzahr/a8b050365d3f5b5a4bee109fead7387d)
- [waybar-gpu.py](https://gist.github.com/Balthazzahr/7106f35202609857aebfce4c4e83f648)
- [waybar-storage.py](https://gist.github.com/Balthazzahr/8bc560106692963e5e7b1ac29dc9b3a5)

## License

MIT License
