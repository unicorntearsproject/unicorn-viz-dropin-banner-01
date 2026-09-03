# banner-01 — Bottom Marquee Banner

**Version 0.9.0**


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
- `Shift+Enter`: insert newline while editing
- `Backspace`: delete previous character while editing
- `Tab`: insert spaces while editing
- `Left/Right`: decrease/increase max line length
- `Up/Down`: decrease/increase font size
- `Ctrl+C`: toggle beat-reactive color
- `Ctrl+R`: reset text to default
- `Esc`: close config modal

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
