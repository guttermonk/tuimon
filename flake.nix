{
  description = "System monitoring scripts for Waybar with rich tooltips";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        packages = {
          waybar-system-monitors = pkgs.callPackage ./default.nix { };
          default = self.packages.${system}.waybar-system-monitors;
        };

        # For development/testing
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            (python3.withPackages (ps: with ps; [ psutil ]))
            lm_sensors
            smartmontools
            dmidecode
            pciutils
          ];
        };
      }
    ) // {
      # Home-manager module
      homeManagerModules = {
        waybar-system-monitors = import ./home-manager-module.nix;
        default = self.homeManagerModules.waybar-system-monitors;
      };

      # NixOS module for system-wide installation
      nixosModules = {
        waybar-system-monitors = { config, lib, pkgs, ... }:
          let
            cfg = config.programs.waybar-system-monitors;
            gpuPackages =
              if cfg.gpu == "amd" then [ pkgs.rocmPackages.rocm-smi ]
              else if cfg.gpu == "intel" then [ pkgs.intel-gpu-tools ]
              else [ ];
          in
          {
            options.programs.waybar-system-monitors = {
              enable = lib.mkEnableOption "Waybar system monitoring scripts";

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
            };

            config = lib.mkIf cfg.enable {
              environment.systemPackages = [
                (pkgs.callPackage ./default.nix { })
              ] ++ gpuPackages;

              # Allow passwordless sudo for dmidecode and smartctl (optional)
              security.sudo.extraRules = [
                {
                  groups = [ "wheel" ];
                  commands = [
                    { command = "${pkgs.dmidecode}/bin/dmidecode"; options = [ "NOPASSWD" ]; }
                    { command = "${pkgs.smartmontools}/bin/smartctl"; options = [ "NOPASSWD" ]; }
                  ];
                }
              ];
            };
          };
        default = self.nixosModules.waybar-system-monitors;
      };

      # Overlay for use in other flakes
      overlays.default = final: prev: {
        waybar-system-monitors = final.callPackage ./default.nix { };
      };
    };
}