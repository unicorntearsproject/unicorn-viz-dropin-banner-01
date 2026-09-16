# banner-01 — Bottom Marquee Banner

**Version 0.14.0**


A subsystem drop-in for unicorn-viz that renders a full-width bottom banner with
runtime configuration and text editing.

## Description

`banner-01` adds a stream-style message strip at the bottom edge of the audience
output. It is loaded as a runtime subsystem and renders after effect content so
it stays visible across all visual scenes.

The banner supports:

- Toggle on/off at runtime
- Config modal with live, multi-line text editing (each line capped at a
  configurable character length, 100 by default)
- One line visible at a time, fading in and out over 1s with a hold in between
- Beat-reactive text color mode, plus a bass/beat-driven "drippy" character wave
- Runtime persistence via shared app state and local JSON fallback
- Primary-display anchoring in multi-head span/mirror modes

## Controls

- `Shift+B`: toggle banner on/off
- `Ctrl+B`: toggle banner config modal
- `Enter`: toggle text edit mode while config is open
- `Esc`: close config modal
- `Alt+B`: toggle beat-reactive color (config modal, not editing)

While the config modal is open but **not** editing:

- `Left/Right`: decrease/increase max line length
- `Up/Down`: decrease/increase font size
- `Ctrl+R`: reset text and max line length to default

While **editing** text (works like a normal textarea):

- `↑↓←→`: move the cursor; `Home`/`End`: start/end of the current line;
  `Ctrl+Home`/`Ctrl+End`: start/end of all the text
- `Ctrl+←→`: jump by word
- `Shift` + any of the above: extend the selection
- `Ctrl+A`: select all
- `Ctrl+C`/`Ctrl+X`/`Ctrl+V`: copy / cut / paste
- `Backspace`/`Delete`: delete before/after the cursor (or the selection,
  if any); `Ctrl+Backspace`: delete the previous word. All repeat while held.
- `Shift+Enter`: insert a newline; `Enter` alone: exit edit mode
- `Tab`: insert spaces
- `Ctrl+R`: reset text and max line length to default

## Config

Example:

```toml
[banner]
enabled = true
text = "Welcome to Unicorn Viz"
max_line_chars = 100
font_px = 28
beat_color_enabled = true
alpha = 0.88
```

Each `\n` in `text` starts a new line in the fade cycle; lines longer than
`max_line_chars` are word-wrapped to fit, same as while typing.

## Runtime Persistence

State is persisted to the app's shared runtime state file (`banner` key in
`runtime/global_state.json`). Banner settings survive restarts automatically.

## Structured Docs

- docs/operations.md
- docs/configuration.md
- docs/integration.md
- docs/troubleshooting.md

## Changelog

- **0.14.0** — Drip animation and beat color join the core config editor's Performance tab as toggles (same state as Alt+D / Alt+B, persisted with the banner's runtime state).
- **0.13.1** — Fixed the config modal's own layout, broken by 0.13.0's
  fixed pixel-offset text placement: at a larger `font_px` (the Up/Down
  hotkey goes to 56) or a narrower window, the "Alt+D: drip" hotkey hint
  (and sometimes "Enter: text edit mode" after it) rendered past the
  panel's right edge — invisible, not just tight — and the Beat
  Color/Drip status fields could overlap with no gap between them,
  reading as words running together. The status row and every hotkey
  hint row now measure each item and wrap to a new line instead of using
  a fixed x position per item, and the panel grows tall enough to fit
  whatever that wrapping needs instead of clipping at a fixed 284px. 3
  new regression tests.
- **0.13.0** — Two changes: (1) the rasterized banner image is now cached
  and reused across frames instead of rebuilt (PIL rasterize + GL texture
  upload) on every single frame regardless of whether anything changed.
  The cache key is the canvas/banner size, the displayed line, a quantized
  fade fraction and beat-color (continuous values collapsed into coarse,
  visually-imperceptible buckets so a held or slowly-fading line reuses
  one image across many frames), and the overall alpha. (2) A new
  **drip toggle** (`Alt+D` in the config modal, **off by default**,
  persisted in runtime state): the character-by-character wave/sag
  animation is opt-in now rather than always-on — when off, the line
  draws as one static string (also cheaper: one draw call instead of one
  per character) and is fully covered by the new cache; when on, nothing
  is cached since the animation changes every frame by design, matching
  the previous always-on behavior exactly. 8 new regression tests.
- **0.12.0** — `enabled` now defaults to `false` (a fresh install
  previously opened with the marquee up, surfaced in the 2026-09-14
  Windows beta); `Shift+B` still toggles it live as before.
- **0.11.0** — Control Room panel (P1 of `docs/planning/control-room-drop-in-integration-plan-2026-09-05.md`): registered on the shared **OVERLAYS** page — line count, max line length, font size, beat color, and TOGGLE / EDIT (opens the audience-window config modal) / RESET buttons mirroring `Shift+B` / `Ctrl+B` / `Ctrl+R`. Unregistered in `shutdown()`.
- **0.10.1** — `Ctrl+R` now also resets `max_line_chars` back to the
  default (100), not just the text. Previously, lowering it (`Left` in the
  config modal, 8 presses from 100 down to the 20 floor) had no way back
  except manually pressing `Right` the same number of times -- surfaced
  live when a session's persisted `max_line_chars` had drifted down to 20
  and "reset text to default" didn't touch it.

- **0.10.0** — The text editor now actually behaves like a textarea, fixing
  three concrete complaints from real use:

  **Selection was invisible.** Shift+move already updated the model
  (`_sel_start`/cursor), but nothing ever drew it, so selecting felt broken
  even though word/line/all-select worked underneath. The editor now
  highlights the selected range.

  **Held keys didn't repeat.** `unicornviz/app.py`'s top-level key dispatch
  drops every `SDL_KEYDOWN` auto-repeat unconditionally, by design, so
  holding a hotkey never re-fires it (holding "N" shouldn't spam "next
  effect"). That blanket filter was also silently eating Backspace/Delete/
  arrow-key repeats inside text editors, where repeating is exactly what's
  wanted — typed characters were never affected (SDL_TEXTINPUT is a
  separate, already-repeating event stream), which is why only Backspace/
  Delete/navigation felt stuck. Fixed narrowly: repeats now reach the
  dispatcher whenever some drop-in has an active text-input handler
  registered (i.e. an editor has focus), leaving ordinary hotkeys
  single-fire everywhere else. Benefits cta-01's editor too, which shares
  the same text-input mechanism.

  **Line-length editing was inconsistent.** `Home`/`End` jumped to the
  start/end of the *entire* text, not the current line (the docstring
  already claimed "current visual line" — the code just didn't do it).
  There was no `Up`/`Down` cursor movement in the editor at all. And the
  textarea double-wrapped: a hard, character-counted `max_line_chars` cap
  (the actual editable unit) layered under a separate pixel-based soft-wrap
  for on-screen display, so what looked like one row didn't correspond to
  what `Home`/`End`/the cap were actually operating on. Replaced with one
  row per logical line (a horizontal scroll-follow within a row instead of
  wrapping it, `_row_window`/`_fit_start`/`_fit_end`), so the visible line
  *is* the editable line. `Home`/`End` now target that line;
  `Ctrl+Home`/`Ctrl+End` go to the start/end of everything; `Up`/`Down`
  move between lines preserving column position (clamped to shorter
  lines, not "sticky" across a multi-line jump — a plain textarea, not an
  IDE). Hitting the cap while typing now flashes the field's border red
  for an instant instead of silently dropping the keystroke with zero
  feedback.

  Also added, since none of it existed: `Ctrl+C`/`Ctrl+X`/`Ctrl+V`
  (copy/cut/paste, via SDL's clipboard) and `Ctrl+Home`/`Ctrl+End`. Fixed a
  stale doc line that still listed `Ctrl+C` as the beat-color toggle (the
  code has always used `Alt+B` for that) — now a real conflict, since
  `Ctrl+C` legitimately means copy while editing.

  27 new regression tests (line navigation, selection ranges incl. the
  standard-but-easy-to-misjudge anchor-persists-across-shift-moves
  semantics, cap-flash, clipboard wiring, the row-fit helpers).

- **0.9.0** — Replaces continuous horizontal scrolling with a multi-line,
  fade-cycled display: side-scrolling depended on a smooth, uninterrupted
  per-frame `dt` to track a pixel offset, which read as visibly jittery
  under the same variable frame pacing a stream encode or CPU contention
  already produces elsewhere. One line is now shown at a time, fading in,
  holding, and fading out over 1s each way, then advancing to the next
  line -- a dropped or delayed frame just leaves the fade fraction slightly
  behind where it "should" be, not a visible stutter in position.

  The editor is now a genuine multi-line model instead of one long string:
  each line is hard-capped at `max_line_chars` (100 by default, configurable,
  `Left`/`Right` in the config modal replacing the old scroll-speed knob) as
  you type -- a newline always goes through, any other character is dropped
  once the line it would land on is already at the cap. Loaded/pasted text
  with over-length lines is word-wrapped to fit rather than truncated.

  The beat-reactive animation gained actual motion, not just color: each
  character now rides a small vertical wave whose amplitude widens with
  bass and gets an extra downward sag on beat hits -- a "drippy" wobble
  instead of a static line.

  `scroll_speed` is gone from the config/state schema (pre-1.0, no
  compatibility shim); `max_line_chars` replaces it as the tunable knob.

- **0.8.1** — fonts now resolve through the shared `unicornviz.fonts` resolver (bundled ui-font.ttf first, then per-platform system dirs including Windows) — fixes tiny bitmap-font fallback on Windows.
- **0.8.0** — initial versioned release.
