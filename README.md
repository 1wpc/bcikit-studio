# BCIKit Studio

BCIKit Studio is an open-source desktop application for non-medical EEG
acquisition with BCIKit hardware. It is intended for teaching, research, and
prototype development: connect a device, configure acquisition, view live
waveforms, add markers, record losslessly, replay sessions, and export data.

It is **not** a medical device and does not provide medical diagnosis.

## What is included

- Chinese-first PySide6 desktop UI with live 8--32 channel visualization.
- BCIKit Wi-Fi TCP, BLE v1, and Raspberry Pi 5 same-machine TCP transports.
- Raw-frame recording, markers, replay, CSV, and BDF+ export.
- Protocol specifications, cross-language test vectors, and a small Python
  reference SDK.
- macOS and Linux ARM64 packaging scripts; Windows source-build instructions.

Firmware, PCB/hardware design files, private development history, and the
project website are deliberately not part of this repository.

## Quick start

Install [uv](https://docs.astral.sh/uv/) and use Python 3.12:

```bash
git clone https://github.com/1wpc/bcikit-studio.git
cd bcikit-studio
uv python install 3.12
uv sync --extra dev
uv run bcikit-studio
```

For full installation, safety, Wi-Fi/BLE connection, recording, replay,
diagnostics, BDF+ export, and troubleshooting guidance, see the
[Studio user guide](docs/BCIKit_Studio_User_Guide.md).

## Build packages

Build on the target operating system; a macOS `.app` cannot be converted into
a Windows `.exe`, or the reverse.

- **macOS Apple Silicon:** run `./scripts/package_macos.sh`. The generated app
  is `dist/macos/BCIKit Studio.app`.
- **Windows:** follow the Windows section of the
  [user guide](docs/BCIKit_Studio_User_Guide.md#42-windows-exe). A signed
  Windows installer is not currently provided, so validate the built
  application on a separate Windows machine before distribution.
- **Raspberry Pi OS 64-bit:** with the matching BCIKit Pi 5 acquisition service
  running on the same device, run `./scripts/package_linux_aarch64.sh` on the
  Pi. The script uses Nuitka directly for compatibility with the Bookworm Qt
  wheel. The standalone directory is `dist/linux-aarch64/main.dist/`; keep all
  its files together. See the [local workstation guide](docs/BCIKit_Studio_User_Guide.md#43-树莓派-5-本机工作站).

## Development checks

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

The test suite uses simulated transports where needed; passing it does not
replace a real-device, electrical-safety, or human-subject validation.

## Protocol and compatibility

Start with [protocol/README.md](protocol/README.md). The wire format and test
vectors are included so other desktop, mobile, and SDK implementations can
remain compatible. Use the configuration returned by a device rather than
assuming a UI selection has taken effect.

## License

Copyright 2026 BCIKit contributors.

Licensed under the [Apache License 2.0](LICENSE).
