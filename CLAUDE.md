# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this is

This is a personal **ZMK firmware configuration** for the **roBa** keyboard — a
split, column-staggered keyboard with an integrated **PMW3610 optical trackball**
on the right half. There is no application code to build or test locally; the
firmware is compiled in the cloud by GitHub Actions and flashed to the keyboard
as `.uf2` files.

Upstream keyboard/shield design: https://github.com/kumamuk-git/zmk-config-roBa

## Hardware topology

- **MCU:** `seeeduino_xiao_ble` (Seeed XIAO nRF52840) on each half.
- **Split:** Two halves — `roBa_L` (left) and `roBa_R` (right). The **right half
  is the central** side (`ZMK_SPLIT_ROLE_CENTRAL`) because it hosts the trackball.
- **Trackball:** PMW3610 sensor on the right half over SPI, driven by the
  external `zmk-pmw3610-driver` module. Left half optionally has an EC11 encoder.
- **Connectivity:** BLE; battery reporting proxied through the central half.
- **ZMK Studio:** enabled (`CONFIG_ZMK_STUDIO=y`) so the keymap can be edited live.

## Repository layout

```
config/
  roBa.keymap          # THE keymap — layers, combos, macros, behaviors (edit this most)
  west.yml             # West manifest: pins ZMK + zmk-pmw3610-driver dependencies
  roBa.json            # Physical layout metadata (used by ZMK Studio / keymap-drawer)
boards/shields/roBa/
  roBa.dtsi            # Shared devicetree: matrix transform, kscan, sensors, SPI/trackball
  roBa_L.overlay       # Left-half GPIO (6 columns) + enables left encoder
  roBa_R.overlay       # Right-half GPIO (5 columns) + enables trackball/SPI/pinctrl
  roBa_L.conf          # Left-half Kconfig (pointing, battery, EC11)
  roBa_R.conf          # Right-half Kconfig (PMW3610 tuning, Studio, battery)
  Kconfig.shield       # Declares SHIELD_ROBA_L / SHIELD_ROBA_R
  Kconfig.defconfig    # Keyboard name + split roles per half
  roBa.zmk.yml         # Shield metadata (id, name, requires xiao_ble)
build.yaml             # GitHub Actions build matrix (which board+shield combos to build)
zephyr/module.yml      # Marks this repo as a Zephyr module (board_root = .)
keymap-drawer/         # Auto-generated keymap SVG + its source YAML
.github/workflows/
  build.yml            # Builds firmware on push/PR/dispatch
  draw.yml             # Regenerates keymap SVG (manual dispatch only)
```

## Keymap: layers, combos, and conventions

All keymap logic lives in `config/roBa.keymap` (devicetree syntax).

**Layers** (order defines the layer index used by `&mo`, `&lt`, `&to`):

| Index | Name           | Purpose                                              |
|-------|----------------|------------------------------------------------------|
| 0     | `default_layer`| Base QWERTY + home-row/thumb mods, Japanese IME keys |
| 1     | `FUNCTION`     | Parens/brackets, symbols, F-keys                     |
| 2     | `NUM`          | Numpad, arithmetic, brackets/braces                  |
| 3     | `ARROW`        | Arrows, navigation, caps-word                        |
| 4     | `MOUSE`        | Mouse buttons — **auto-mouse layer** for trackball   |
| 5     | `SCROLL`       | Trackball scroll layer                               |
| 6     | `layer_6`      | Bluetooth profiles, bootloader, `bt` clears          |

> **Layer indices are load-bearing.** `&trackball` in the keymap sets
> `automouse-layer = <4>` and `scroll-layers = <5>`, and many keys use numeric
> `&lt`/`&mo`/`&to` (e.g. `&lt 5 I`, `&lt 4 HASH`). If you **reorder, add, or
> remove a layer**, update every numeric reference *and* the trackball's
> `automouse-layer`/`scroll-layers` accordingly.

**Notable custom behaviors/macros:**
- `to_layer_0` — macro (`behavior-macro-one-param`) that jumps to layer 0 while
  passing a keycode (used for Japanese IME toggles like `INT_MUHENKAN`).
- `lt_to_layer_0` — hold-tap: hold = momentary layer, tap = return to layer 0.
- Combos: `TAB`, `Shift+TAB`, muhenkan, double-quote, `=` — defined via
  `key-positions` (positions are matrix-transform indices, not physical keys).

**Global behavior tweaks** live at the top of the file: `&mt` is set to
`balanced` flavor with `quick-tap-ms = 0`; `&trackball` sets the auto-mouse and
scroll layers.

## How firmware gets built

There is no local build. `build.yaml` drives the CI matrix in
`.github/workflows/build.yml` (which calls the shared
`zmkfirmware/zmk/build-user-config.yml`). Current targets:

- `roBa_R` (with `studio-rpc-usb-uart` snippet — enables ZMK Studio over USB)
- `roBa_L`
- `settings_reset` (utility firmware to wipe BLE/settings)

To build: push to the branch or trigger the **Build** workflow manually, then
download the `firmware` artifact and flash the per-half `.uf2` files.

## The keymap drawing (SVG)

`keymap-drawer/roBa.svg` (shown in `README.md`) is **generated**, not hand-edited.
The **Draw Keymap** workflow (`.github/workflows/draw.yml`) is **manual-dispatch
only** — it does not run automatically on push (the `push:` trigger is commented
out). So after changing `config/roBa.keymap`, the committed SVG will be stale
until someone runs that workflow. Do not hand-edit `roBa.svg` or `roBa.yaml`.

## Working conventions for AI assistants

- **Editing the keymap is the common task.** Preserve the existing column
  alignment/whitespace in the `bindings` blocks — it is intentional and makes the
  layout readable. Keep the 4-row × (5+6 / 6+5) grid shape intact.
- **Respect the split column split:** left overlay defines 6 columns, right
  overlay defines 5 (with `col-offset = <6>` on the right transform). The full
  matrix transform in `roBa.dtsi` is `columns = <11>, rows = <4>`.
- **Trackball/pointer tuning** (CPI, scroll, auto-mouse timeout, orientation)
  lives in `boards/shields/roBa/roBa_R.conf` as `CONFIG_PMW3610_*` options.
- **Dependencies** are pinned in `config/west.yml` (`zmk` and
  `zmk-pmw3610-driver`, both tracking `main`). Add new ZMK modules here.
- **Don't invent local build/test commands** — validation happens through CI.
  When unsure whether a keymap change compiles, note that it will be verified by
  the Build workflow.

## Git / workflow

- Develop on the assigned feature branch; commit with clear messages; push with
  `git push -u origin <branch>`.
- Do **not** open a pull request unless explicitly asked.
- Comments in some files are in Japanese (the maintainer's language) — this is
  expected; keep existing comments intact when editing nearby.
