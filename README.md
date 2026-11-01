# ZZMMI

Are you one of the few people who play ZZZ on a Mac? Do you wish you could run mods as well? Well look no further!

ZZMMI is a ZZZ mod loader specifically designed for Mac. It's compatible with [XXMI](https://github.com/SpectrumQT/XXMI-Launcher),
so any mods made for that or for ZZMI should work without a hitch.

It runs ZZZ through [Yaagl](https://github.com/yaagl/yet-another-anime-game-launcher)
(Wine + [DXMT](https://github.com/3Shain/dxmt), D3D11->Metal ZZZ runs on macOS through ). 
ZZMMI runs XXMI's real 3DMigoto build inside that Wine runtime, on top of DXMT, and manages it from a native macOS CLI.

It currently works on loading character model mods, and most likely other mods as well (haven't tested that many yet.) If
you find any mods which don't work, open an issue and we can add in support for that as well.

## Requirements

- Apple Silicon Mac with ZZZ installed and working through **Yaagl ZZZ OS**
- Python 3.11+
- For now, XXMI Launcher (ZZMI) installed inside Yaagl's Wine prefix (`C:\XXMI Launcher`)

## Usage

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/zzmmi doctor            # check the environment
.venv/bin/zzmmi debug-launch      # launch with mods and full logging
.venv/bin/zzmmi restore           # undo Wine DLL swaps and proxy links after an interrupted session
```

Put mods where you would usually for XXMI (that is, put them in `XXMI Launcher\ZZMI\Mods`)

## Disclaimer

Modifying the game client violates HoYoverse's Terms of Service. Running under Wine doesn't change that.
Use at your own risk.

## Support
Want to support my work and my coffee addiction? By me a cup [here](https://ko-fi.com/imnotjahan)!
