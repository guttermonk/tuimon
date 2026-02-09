{ lib, stdenvNoCC, python3, makeWrapper, lm_sensors, procps, dmidecode, smartmontools, pciutils }:

stdenvNoCC.mkDerivation {
  pname = "waybar-system-monitors";
  version = "1.0.0";

  src = ./scripts;

  nativeBuildInputs = [ makeWrapper ];

  buildInputs = [
    (python3.withPackages (ps: with ps; [ psutil ]))
  ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/bin
    mkdir -p $out/share/waybar-system-monitors

    # Install scripts
    for script in waybar-cpu.py waybar-memory.py waybar-gpu.py waybar-storage.py; do
      if [ -f "$script" ]; then
        install -Dm755 "$script" "$out/share/waybar-system-monitors/$script"
      fi
    done

    # Create wrapper scripts with proper PATH
    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-cpu \
      --add-flags "$out/share/waybar-system-monitors/waybar-cpu.py" \
      --prefix PATH : "${lib.makeBinPath [ lm_sensors procps ]}"

    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-memory \
      --add-flags "$out/share/waybar-system-monitors/waybar-memory.py" \
      --prefix PATH : "${lib.makeBinPath [ lm_sensors dmidecode ]}"

    # GPU script needs: nvidia-smi (user provides), lspci for detection, sensors for temps
    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-gpu \
      --add-flags "$out/share/waybar-system-monitors/waybar-gpu.py" \
      --prefix PATH : "${lib.makeBinPath [ lm_sensors pciutils ]}"

    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-storage \
      --add-flags "$out/share/waybar-system-monitors/waybar-storage.py" \
      --prefix PATH : "${lib.makeBinPath [ lm_sensors smartmontools ]}"

    runHook postInstall
  '';

  meta = with lib; {
    description = "System monitoring scripts for Waybar with rich tooltips (supports Nvidia, AMD, and Intel GPUs)";
    homepage = "https://github.com/Balthazzahr";
    license = licenses.mit;
    platforms = platforms.linux;
    maintainers = [ ];
  };
}