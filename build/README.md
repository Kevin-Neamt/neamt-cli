# build/ — desktop packaging resources

electron-builder reads this directory (`buildResources`) when producing the
installers. Packaged output goes to `release/` (git-ignored).

## Files

- `entitlements.mac.plist` — hardened-runtime entitlements for the notarized
  macOS build. Required because Neamt spawns a child Python process.

## Icons

App icons live in `../assets/`:

| File             | Used by        |
| ---------------- | -------------- |
| `icon.icns`      | `build:mac`    |
| `icon.ico`       | `build:win`    |
| `icon.png` (1024)| dev dock/window |

All three are generated from `~/Desktop/logo.png`.

## Bundling Python (production)

`npm start` (development) uses the system / venv Python. The packaged app looks
for a self-contained interpreter at `Resources/app/python/` first and only
falls back to system Python.

To ship a fully self-contained app, drop a relocatable interpreter
(e.g. from [python-build-standalone](https://github.com/indygreg/python-build-standalone))
into `python/` before running `build:mac` / `build:win`, then
`python/bin/python3 -m pip install -r requirements.txt`. The `python/` folder is
git-ignored; it is added to the bundle via the `extraResources` filter.

## Commands

```bash
npm install          # electron + electron-builder
npm start            # run the app against system Python (dev)
npm run build:mac    # → release/Neamt-<version>.dmg + .zip
npm run build:win    # → release/Neamt Setup <version>.exe
```
