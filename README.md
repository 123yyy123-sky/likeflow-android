# LikeFlow

<p align="center">
  <img src="assets/likeflow.png" width="112" alt="LikeFlow icon">
</p>

<p align="center">
  Windows desktop UI for configurable Android like automation.
</p>

LikeFlow wraps the existing ADB, UIAutomator2 and OpenCV runner in a desktop
application. It is designed for repeatable UI testing on an Android device or
emulator that is already open on the target page.

## Features

- Configure the total number of likes.
- Select the unliked icon template and an optional liked-state template.
- Choose an ADB device and ADB directory.
- Configure rounds, per-round actions, scroll limits and fast mode.
- Start, stop and monitor a run from the desktop UI.
- Save settings between launches.
- Show real-time progress and logs.
- Build a standalone Windows executable with PyInstaller.

## Quick Start

Requirements:

- Windows 10/11
- Python 3.10+
- Android platform tools / ADB
- USB debugging or an emulator with ADB enabled

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Start the application:

```powershell
python app.py
```

The target page must already be open before starting a task. LikeFlow does not
launch the Android app or navigate between accounts.

## Configure Templates

Capture a screen:

```powershell
python qq_like_test.py --serial emulator-5554 --capture-screen
```

Crop a tight image of the unliked icon and select it as **未点赞图型**. The liked
template is optional and is only used to verify whether a click changed the
control state.

The default fast template scales and threshold are tuned for the bundled
examples. If your device resolution or icon scale differs, use the standard mode
first and adjust the template crop.

## Build Windows EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The executable is written to:

```text
dist\LikeFlow.exe
```

`LikeFlow.spec` bundles the UIAutomator2 runtime assets required to connect to
an Android device. Build through the spec file rather than a minimal
`pyinstaller app.py` command.

## Command Line

The GUI launches `batch_test.py` through the same executable. The CLI remains
available for diagnostics:

```powershell
python batch_test.py `
  --serial emulator-5554 `
  --adb-dir "F:\模拟器\LDPlayer14" `
  --template templates\unliked_heart.png `
  --liked-template templates\liked_heart.png `
  --rounds 5 `
  --per-round 100 `
  --max-scrolls-per-round 30 `
  --min-total-clicks 1450 `
  --fast
```

## Project Layout

```text
app.py                 Tkinter desktop application
batch_test.py          Bounded multi-round batch runner
qq_like_test.py        ADB screenshot, detection and click worker
templates/             Example icon templates
assets/                Application icon and branding
build_exe.ps1          Windows packaging script
.github/workflows/     GitHub Actions Windows build
```

## Configuration

The desktop application saves settings to:

```text
%APPDATA%\LikeFlow\config.json
```

Run artifacts are written under `%APPDATA%\LikeFlow\artifacts` by default, so
the executable does not need write access beside itself.

## Responsible Use

Use this project only with accounts, devices, pages and data that you own or are
authorized to test. Automated interactions must comply with the target
application's terms, rate limits and anti-abuse policies. Do not use it for
inauthentic engagement, spam or activity on other people's accounts.

## License

MIT. See [LICENSE](LICENSE).
