# Building MotecLogGenerator

This project can be packaged for both macOS and Windows with PyInstaller.
The primary desktop target is now the PySide6 app.

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
pyinstaller --noconfirm --clean motec_log_generator_qt.spec
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
PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller-config pyinstaller --noconfirm --clean motec_log_generator_qt.spec
```

Expected outputs:

- `dist/MotecLogGeneratorQt.app`
- `dist/MotecLogGeneratorQt/`

The `.app` bundle is the main macOS deliverable.

### 3. Optional code signing

If you have an Apple signing identity, you can pass it into the build:

```bash
export APPLE_CODESIGN_IDENTITY="Developer ID Application: Your Name Here"
PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller-config pyinstaller --noconfirm --clean motec_log_generator_qt.spec
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
pyinstaller --noconfirm --clean motec_log_generator_qt.spec
```

Expected outputs:

- `dist\MotecLogGeneratorQt.exe`

If SmartScreen reputation matters for your distribution, signing the `.exe` is recommended.

## GitHub Actions

The repository includes a workflow:

- `.github/workflows/build-binaries.yml`

It builds:

- `MotecLogGeneratorQt.app` on macOS
- `MotecLogGeneratorQt.exe` on Windows

You can run it from the Actions tab or on push/PR events depending on your workflow settings.

## Notes About Dependencies

The Qt app depends on:

- `PySide6` for the desktop interface
- `libxrk` for AIM XRK/XRZ import
- `numpy` and related runtime dependencies used by the converter backend

The Qt PyInstaller spec already collects the `libxrk` native pieces needed by the packaged app and bundles the platform icon assets for macOS and Windows.

## Troubleshooting

### PyInstaller cache permission issues on macOS

If PyInstaller fails while trying to write under the default application-support cache path, use:

```bash
PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller-config
```

### Windows builds from macOS

PyInstaller does not cross-compile Windows executables from macOS. To produce the real `.exe`, build on Windows itself or let the included GitHub Actions workflow run on `windows-latest`.
