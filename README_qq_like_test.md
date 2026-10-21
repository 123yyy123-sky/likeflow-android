# QQ test mini-program like/scroll automation

This is an Android UI smoke test for a newly built test mini-program. It starts
from the page currently open in QQ; it does not launch QQ or navigate accounts.

## Prerequisites

- An Android phone or emulator connected through ADB.
- USB debugging enabled.
- QQ opened manually to the test mini-program feed.
- Python 3.10 or newer.

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Prepare an icon template

Capture the current page:

```powershell
python qq_like_test.py --serial emulator-5554 --capture-screen
```

Crop one unliked like icon from the saved PNG and save it as `like_icon.png`.
Use a tight crop with little background. The template is only a fallback; the
script first looks for labels such as `赞`, `点赞`, `喜欢`, or `Like` in the UI
tree.

An optional liked-state template can be supplied with `--liked-template`.
After each click, the script checks for that template near the clicked point
and records the result in `result.json` as `likes_verified`.

## Run the test

Dry run, which detects but does not tap:

```powershell
python qq_like_test.py `
  --serial emulator-5554 `
  --template like_icon.png `
  --dry-run `
  --i-confirm-test-account
```

Click and scroll with conservative limits:

```powershell
python qq_like_test.py `
  --serial emulator-5554 `
  --template like_icon.png `
  --liked-template liked_icon.png `
  --expected-text "TEST ENV" `
  --max-actions 12 `
  --max-scrolls 30 `
  --i-confirm-test-account
```

Every run writes screenshots, `run.log`, and `result.json` under
`artifacts/like_test/<timestamp>/`.

## Bounded emulator batch

`batch_test.py` runs configurable rounds and clicks on any connected Android
device. Omit `--serial` to use the only connected device. By default it keeps
starting extra rounds until at least 1450 clicks have been made; it stops with
exit code 3 if two consecutive rounds make no progress first.

```powershell
python batch_test.py `
  --serial 127.0.0.1:7555 `
  --template templates/unliked_heart.png `
  --liked-template templates/liked_heart.png `
  --rounds 5 `
  --per-round 10 `
  --min-total-clicks 1450
```

For a faster run, use larger worker batches and `--fast`:

```powershell
python batch_test.py `
  --serial emulator-5554 `
  --template templates/unliked_heart.png `
  --liked-template templates/liked_heart.png `
  --rounds 5 `
  --per-round 100 `
  --max-scrolls-per-round 30 `
  --min-total-clicks 1450 `
  --fast
```

`--fast` reduces click/scroll waits, skips per-step screenshot files, and uses a
smaller template scale set. If detection becomes unreliable, omit `--fast`.

## Notes

- Omit `--serial` when only one Android device is connected.
- `--package-prefix` is recorded as a package label and no longer blocks runs in
  a different foreground package.
- `--dry-run` is the recommended first pass for checking selectors and template
  accuracy.
- Each worker round caps at 100 clicks and 100 swipes. The batch runner can run
  additional rounds to reach `--min-total-clicks`.
