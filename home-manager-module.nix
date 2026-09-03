{ config, options, lib, pkgs, ... }:

let
  cfg = config.programs.waybar-system-monitors;

  # Waybar accepts an integer number of seconds or the string "once"
  intervalType = lib.types.nullOr (lib.types.either lib.types.ints.positive (lib.types.enum [ "once" ]));

  # Per-module interval resolution:
  # 1. explicit per-module option
  # 2. global `interval` if the user set it (keeps old configs behaving identically)
  # 3. per-module default
  # The option's own default is injected as a definition at mkOptionDefault
  # priority, so isDefined can't distinguish "user set it" - highestPrio can.
  globalIntervalSet =
    options.programs.waybar-system-monitors.interval.highestPrio
      < (lib.mkOptionDefault null).priority;
  resolveInterval = per: moduleDefault:
    if per != null then per
    else if globalIntervalSet then cfg.interval
    else moduleDefault;
  
  waybar-system-monitors = pkgs.callPackage ./default.nix { };

  # Build one monitor's waybar module in whichever mode `continuous` selects.
  # The two shapes are mutually exclusive on waybar's side: `interval` makes it
  # re-execute the binary, while a continuous script is read from stdout and
  # must not carry an `interval` (see waybar-custom(5), which notes that
  # restart-interval "can't be used with the interval option").
  mkMonitor = { bin, args ? "", interval, extra ? { } }:
    let
      # `interval = "once"` means run a single time -- there is nothing to loop,
      # so such a module stays one-shot even when continuous mode is on.
      useLoop = cfg.continuous && builtins.isInt interval;
      mode =
        if useLoop then {
          exec = "${waybar-system-monitors}/bin/${bin}-loop ${toString interval}${args}";
          restart-interval = 5;
        } else {
          exec = "${waybar-system-monitors}/bin/${bin}${args}";
          inherit interval;
        };
    in
    mode // {
      format = "{}";
      return-type = "json";
      tooltip = true;
    } // extra;

  # Known TUI file managers that need to be wrapped in a terminal
  tuiFileManagers = [ "yazi" "ranger" "lf" "nnn" "mc" "vifm" "fff" ];
  isTuiFileManager = lib.elem cfg.fileManager tuiFileManagers;
  
  # Build the file manager command
  fileManagerCommand = 
    if cfg.fileManager == "xdg-open" then "${pkgs.xdg-utils}/bin/xdg-open ~"
    else if isTuiFileManager then "${cfg.terminal} -e ${cfg.fileManager} ~"
    else "${cfg.fileManager} ~";

  # GPU-specific packages based on vendor selection
  gpuPackages = lib.optionals cfg.enableGpu (
    if cfg.gpu == "amd" then [ pkgs.rocmPackages.rocm-smi ]
    else if cfg.gpu == "intel" then [ pkgs.intel-gpu-tools ]
    else [ ]  # nvidia uses nvidia-smi which comes with drivers, or null for auto-detect
  );
  
  # Default colors (Catppuccin-inspired)
  defaultColors = {
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

  tomlFormat = pkgs.formats.toml { };
in
{
  options.programs.waybar-system-monitors = {
    enable = lib.mkEnableOption "Waybar system monitoring scripts";

    enableCpu = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable CPU monitoring module";
    };

    cpuDisplay = lib.mkOption {
      type = lib.types.enum [ "temp" "percent" "both" ];
      default = "temp";
      description = ''
        What to display in the CPU waybar module.
        - "temp": Show temperature only
        - "percent": Show utilization percentage only
        - "both": Show both temperature and utilization
      '';
      example = "both";
    };

    enableMemory = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable Memory monitoring module";
    };

    enableGpu = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable GPU monitoring module (supports Nvidia, AMD, and Intel GPUs)";
    };

    gpuDisplay = lib.mkOption {
      type = lib.types.enum [ "temp" "percent" "both" ];
      default = "temp";
      description = ''
        What to display in the GPU waybar module.
        - "temp": Show temperature only
        - "percent": Show utilization percentage only
        - "both": Show both temperature and utilization
      '';
      example = "both";
    };

    gpu = lib.mkOption {
      type = lib.types.nullOr (lib.types.enum [ "nvidia" "amd" "intel" ]);
      default = null;
      description = ''
        GPU vendor for installing additional monitoring tools.
        - "nvidia": No extra tools needed (uses nvidia-smi from drivers)
        - "amd": Installs rocm-smi for better AMD GPU monitoring
        - "intel": Installs intel-gpu-tools for Intel GPU utilization stats
        - null: Auto-detect without installing extra tools
      '';
      example = "amd";
    };

    enableStorage = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable Storage monitoring module";
    };

    colors = lib.mkOption {
      type = tomlFormat.type;
      default = defaultColors;
      description = "Color configuration for the monitoring scripts";
      example = lib.literalExpression ''
        {
          colors = {
            normal = {
              red = "#ff0000";
              green = "#00ff00";
              # ...
            };
            bright = {
              red = "#ff5555";
              # ...
            };
          };
        }
      '';
    };

    terminal = lib.mkOption {
      type = lib.types.str;
      default = "alacritty";
      description = "Terminal emulator to use when opening btop on click";
    };

    fileManager = lib.mkOption {
      type = lib.types.str;
      default = "xdg-open";
      description = ''
        File manager to use when clicking the storage module.
        - "xdg-open": Use system default (works for GUI file managers)
        - "yazi", "ranger", "lf", "nnn", "mc": TUI file managers (will be wrapped in terminal)
        - Or any custom command
      '';
      example = "yazi";
    };

    continuous = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Run each monitor as a single long-lived process instead of letting
        waybar re-execute it every interval.

        One-shot (the default) starts a fresh Python interpreter and re-imports
        psutil on every tick. Continuous mode starts one process per monitor
        which ticks itself, so that cost is paid once at waybar startup and only
        the measurement work repeats. Measured on a Haswell laptop, ten ticks of
        the CPU monitor cost 1.83s of CPU as ten processes versus 0.62s in one.

        The intervals below mean the same thing in both modes and the output is
        identical, so this can be flipped back and forth to compare. Continuous
        mode drops waybar's `interval` key (waybar reads the process's stdout
        instead) and sets `restart-interval`, so a monitor that dies is
        respawned rather than leaving the module blank forever.
      '';
    };

    interval = lib.mkOption {
      type = lib.types.int;
      default = 2;
      description = ''
        Update interval in seconds for waybar modules.
        Applies to every module that does not have its own *Interval option set.
        If left unset, each module uses its own default instead
        (cpu: 2, gpu: 2, memory: 3, storage: 60).
      '';
    };

    cpuInterval = lib.mkOption {
      type = intervalType;
      default = null;
      description = ''
        Update interval in seconds for the CPU module, or "once" for a single run.
        Falls back to `interval` if set, otherwise 2.
      '';
      example = 5;
    };

    gpuInterval = lib.mkOption {
      type = intervalType;
      default = null;
      description = ''
        Update interval in seconds for the GPU module, or "once" for a single run.
        Falls back to `interval` if set, otherwise 2.
      '';
      example = 5;
    };

    memoryInterval = lib.mkOption {
      type = intervalType;
      default = null;
      description = ''
        Update interval in seconds for the Memory module, or "once" for a single run.
        Falls back to `interval` if set, otherwise 3.
        The static DIMM inventory (dmidecode) is cached per boot regardless of this
        setting; the interval only governs the live readings (usage, temperatures).
        Note that a very high value or "once" freezes those live readings.
      '';
      example = 10;
    };

    storageInterval = lib.mkOption {
      type = intervalType;
      default = null;
      description = ''
        Update interval in seconds for the Storage module, or "once" for a single run.
        Falls back to `interval` if set, otherwise 60.
        SMART health/wear data (a heavy `smartctl -a` read) is cached for an hour
        regardless of this setting; the interval only governs usage, I/O speeds,
        and temperature.
      '';
      example = 120;
    };

    plain = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Output plain text without Pango color markup.
        Enable this to use your own CSS colors instead of inline colors.
      '';
    };

    waybarConfig = lib.mkOption {
      type = lib.types.attrs;
      readOnly = true;
      description = "Generated waybar module configurations to add to your waybar config";
    };
  };

  config = lib.mkIf cfg.enable {
    # Install the package and optional GPU tools
    home.packages = [ waybar-system-monitors ] ++ gpuPackages;

    # Create color config file
    xdg.configFile."waybar/colors.toml".source = tomlFormat.generate "colors.toml" cfg.colors;

    # Set TERMINAL env var if specified
    home.sessionVariables = lib.mkIf (cfg.terminal != "") {
      TERMINAL = cfg.terminal;
    };

    # Generate waybar config that can be merged
    programs.waybar-system-monitors.waybarConfig = {
      "custom/cpu" = lib.mkIf cfg.enableCpu (mkMonitor {
        bin = "waybar-cpu";
        args = " --display=${cfg.cpuDisplay}${lib.optionalString cfg.plain " --plain"}";
        interval = resolveInterval cfg.cpuInterval 2;
        extra.on-click = "${cfg.terminal} -e btop";
      });

      "custom/memory" = lib.mkIf cfg.enableMemory (mkMonitor {
        bin = "waybar-memory";
        args = lib.optionalString cfg.plain " --plain";
        interval = resolveInterval cfg.memoryInterval 3;
      });

      "custom/gpu" = lib.mkIf cfg.enableGpu (mkMonitor {
        bin = "waybar-gpu";
        args = " --display=${cfg.gpuDisplay}${lib.optionalString cfg.plain " --plain"}";
        interval = resolveInterval cfg.gpuInterval 2;
        extra.on-click = "${cfg.terminal} -e btop";
      });

      "custom/storage" = lib.mkIf cfg.enableStorage (mkMonitor {
        bin = "waybar-storage";
        args = lib.optionalString cfg.plain " --plain";
        interval = resolveInterval cfg.storageInterval 60;
        extra.on-click = fileManagerCommand;
      });
    };
  };
}