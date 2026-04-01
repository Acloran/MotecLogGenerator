# Building MotecLogGenerator

This project can be packaged for both macOS and Windows with PyInstaller.

## Quick Start

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the build dependencies:

```bash
python -m pip install -r requirements-build.txt
```

Build the desktop application:

```bash
pyinstaller --noconfirm --clean motec_log_generator.spec
```

Artifacts are written to `dist/`.

## macOS Build

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
```

### 2. Build the app bundle

```bash
PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller-config pyinstaller --noconfirm --clean motec_log_generator.spec
```

Expected outputs:

- `dist/MotecLogGenerator.app`
- `dist/MotecLogGenerator/`

The `.app` bundle is the main macOS deliverable.

### 3. Optional code signing

If you have an Apple signing identity, you can pass it into the build:

```bash
export APPLE_CODESIGN_IDENTITY="Developer ID Application: Your Name Here"
PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller-config pyinstaller --noconfirm --clean motec_log_generator.spec
```

For broader distribution outside your own machine, Apple notarization is also recommended after signing.

## Windows Build

Build the Windows executable on Windows itself, or in GitHub Actions.

### 1. Create and activate a virtual environment

PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
python -m pip install -r requirements-build.txt
```

### 3. Build

```powershell
pyinstaller --noconfirm --clean motec_log_generator.spec
```

Expected outputs:

- `dist\MotecLogGenerator.exe`

If SmartScreen reputation matters for your distribution, signing the `.exe` is recommended.

## GitHub Actions

The repository includes a workflow:

- `.github/workflows/build-binaries.yml`

It builds:

- `MotecLogGenerator.app` on macOS
- `MotecLogGenerator.exe` on Windows

You can run it from the Actions tab or on push/PR events depending on your workflow settings.

## Notes About Dependencies

The GUI now depends on:

- `matplotlib` for the large preview graph
- `tkinterdnd2-universal` for drag and drop
- `libxrk` for AIM XRK/XRZ import

The PyInstaller spec already collects the extra `tkinterdnd2` runtime files and the `libxrk` native pieces needed by the packaged app.

## Troubleshooting

### Tk / drag-and-drop issues on macOS

If drag and drop does not initialize correctly, make sure the environment uses:

- `tkinterdnd2-universal`

and not an older `tkinterdnd2` wheel with an incompatible `tkdnd` binary.

### PyInstaller cache permission issues on macOS

If PyInstaller fails while trying to write under the default application-support cache path, use:

```bash
PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller-config
```

### Large build size

The preview graph uses `matplotlib`, so packaged builds are larger than a minimal Tkinter-only app. That is expected.
