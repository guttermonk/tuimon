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
    for script in waybar-cpu.py waybar-memory.py waybar-gpu.py waybar-storage.py tuimon-loop.py; do
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

    # Continuous variants: same monitors, but driven by tuimon-loop.py so the
    # interpreter and `import psutil` are paid once instead of on every tick.
    # Waybar reads these as long-running scripts (omit `interval`, use the
    # module's `continuous` option). Measured on a Haswell laptop, ten ticks of
    # waybar-cpu cost 1.83s CPU as ten processes versus 0.62s in one.
    #
    # Invoked as: waybar-cpu-loop <interval-seconds> [script args...]
    # The one-shot binaries above are untouched, so both modes stay comparable.
    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-cpu-loop \
      --add-flags "$out/share/waybar-system-monitors/tuimon-loop.py $out/share/waybar-system-monitors/waybar-cpu.py" \
      --prefix PATH : "${lib.makeBinPath [ lm_sensors procps ]}"

    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-memory-loop \
      --add-flags "$out/share/waybar-system-monitors/tuimon-loop.py $out/share/waybar-system-monitors/waybar-memory.py" \
      --prefix PATH : "${lib.makeBinPath [ lm_sensors dmidecode ]}"

    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-gpu-loop \
      --add-flags "$out/share/waybar-system-monitors/tuimon-loop.py $out/share/waybar-system-monitors/waybar-gpu.py" \
      --prefix PATH : "${lib.makeBinPath [ lm_sensors pciutils ]}"

    makeWrapper ${python3.withPackages (ps: with ps; [ psutil ])}/bin/python3 $out/bin/waybar-storage-loop \
      --add-flags "$out/share/waybar-system-monitors/tuimon-loop.py $out/share/waybar-system-monitors/waybar-storage.py" \
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