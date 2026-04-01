# MotecLogGenerator

Utility for generating MoTeC `.ld` files that can be opened in [MoTeC i2 Pro](https://www.motec.com.au/i2/i2overview/) from several external log formats.

The app now supports:
- Raw CAN bus logs with a matching DBC
- Generic CSV logs
- [COBB Accessport](https://www.cobbtuning.com/products/accessport) CSV logs
- AIM `.xrk` and `.xrz` telemetry logs

AIM support is implemented with [`libxrk`](https://github.com/m3rlin45/libxrk), a GitHub-hosted parser for AIM telemetry files.

## What Changed

- Added a cleaner desktop GUI with clearer layout and better input flow.
- Rebuilt the GUI around a dark, preview-first workflow with large plotting, trim/split controls, and batch metadata editing.
- Added AIM `.xrk/.xrz` import support and conversion into MoTeC `.ld`.
- Added drag-and-drop file intake and per-file progress/status inside the file list.
- Made the file picker react to the selected input type so it filters for the right files automatically.
- Hardened the parsers so missing or malformed samples are skipped more gracefully.
- Improved conversion performance by avoiding repeated NumPy appends while writing MoTeC channels.
- Added build files for Windows and macOS packaging with PyInstaller.

## Requirements

- Python 3.14 recommended
- Tkinter
- matplotlib
- tkinterdnd2-universal
- Runtime dependencies from [requirements.txt](/Users/acloran/Documents/GitHub/MotecLogGenerator/requirements.txt)

Install runtime dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Running The App

Launch the GUI:

```bash
python motec_log_generator.py
```

The GUI lets you:
- Drag and drop files or folders directly into the queue
- Preview a selected file on a large graph that defaults to a detected speed channel
- Crop or split a session into two exported segments
- Batch-edit MoTeC metadata across multiple selected files
- Track status directly on each file row instead of using a console pane
- Provide a DBC only when CAN logs are involved

## CLI Usage

### CAN Bus Logs

```bash
python motec_log_generator.py /path/to/my/data/can_data.log CAN --dbc /path/to/my/data/car.dbc
```

### CSV Logs

```bash
python motec_log_generator.py /path/to/my/data/csv_data.csv CSV
```

The first column of the CSV file must be time in seconds.

### Accessport Logs

```bash
python motec_log_generator.py /path/to/my/data/accessport_data.csv ACCESSPORT
```

### AIM XRK/XRZ Logs

```bash
python motec_log_generator.py /path/to/my/data/session.xrk AIM
```

### Optional Output Path

Single-file mode:

```bash
python motec_log_generator.py /path/to/log.csv CSV --output /path/to/output/custom_name.ld
```

Directory mode:

```bash
python motec_log_generator.py /path/to/log_folder CSV --output /path/to/output_folder
```

### Metadata Options

```text
usage: motec_log_generator.py [-h] [--output OUTPUT] [--frequency FREQUENCY]
                              [--dbc DBC] [--driver DRIVER]
                              [--vehicle_id VEHICLE_ID]
                              [--vehicle_weight VEHICLE_WEIGHT]
                              [--vehicle_type VEHICLE_TYPE]
                              [--vehicle_comment VEHICLE_COMMENT]
                              [--venue_name VENUE_NAME]
                              [--event_name EVENT_NAME]
                              [--event_session EVENT_SESSION]
                              [--long_comment LONG_COMMENT]
                              [--short_comment SHORT_COMMENT]
                              log {CAN,CSV,ACCESSPORT,AIM}
```

If an AIM file contains session metadata, the converter will use it as a fallback when the equivalent MoTeC fields are left blank.

## Building Desktop Artifacts

Build dependencies live in [requirements-build.txt](/Users/acloran/Documents/GitHub/MotecLogGenerator/requirements-build.txt).

A dedicated build guide is also available in [BUILDING.md](/Users/acloran/Documents/GitHub/MotecLogGenerator/BUILDING.md).

Install them:

```bash
python -m pip install -r requirements-build.txt
```

Build with the included PyInstaller spec file [motec_log_generator.spec](/Users/acloran/Documents/GitHub/MotecLogGenerator/motec_log_generator.spec):

```bash
pyinstaller --noconfirm --clean motec_log_generator.spec
```

Expected outputs:
- Windows: `dist/MotecLogGenerator.exe`
- macOS: `dist/MotecLogGenerator.app`

The spec file explicitly bundles the native `libxrk` pieces needed for AIM file support.

## GitHub Actions Build

The workflow [build-binaries.yml](/Users/acloran/Documents/GitHub/MotecLogGenerator/.github/workflows/build-binaries.yml) builds release artifacts on both platforms:
- Windows artifact: `MotecLogGenerator-windows`
- macOS artifact: `MotecLogGenerator-macos`

You can trigger it manually from the Actions tab with `workflow_dispatch`, or let it run on pushes and pull requests.

## macOS Deployment Notes

For local use, the generated `.app` is enough. If you want to distribute it outside your own machine, you will usually also want to:
- Code-sign the app bundle
- Notarize it with Apple

The spec file supports passing an `APPLE_CODESIGN_IDENTITY` environment variable if you want PyInstaller to sign during the build.

## Windows Deployment Notes

The generated `.exe` is intended as a standalone deliverable. If Windows SmartScreen is a concern for distribution, signing the executable with a code-signing certificate is still recommended.

## Examples

Sample input files live in [examples](/Users/acloran/Documents/GitHub/MotecLogGenerator/examples).

## Disclaimer

This work was produced for research purposes. It should not be used to circumvent MoTeC licensing requirements for their data loggers or i2 software.
