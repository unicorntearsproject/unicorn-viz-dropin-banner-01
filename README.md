# banner-01 — Bottom Marquee Banner

A subsystem drop-in for unicorn-viz that renders a full-width bottom marquee with
runtime configuration and text editing.

## Description

`banner-01` adds a stream-style ticker at the bottom edge of the audience output.
It is loaded as a runtime subsystem and renders after effect content so it stays
visible across all visual scenes.

The banner supports:

- Toggle on/off at runtime
- Config modal with live text editing
- Beat-reactive text color mode
- Runtime persistence via shared app state and local JSON fallback
- Primary-display anchoring in multi-head span/mirror modes

## Controls

- `B`: toggle banner on/off
- `Ctrl+B`: toggle banner config modal
- `Enter`: toggle text edit mode while config is open
- `Shift+Enter`: insert newline while editing
- `Backspace`: delete previous character while editing
- `Tab`: insert spaces while editing
- `Left/Right`: decrease/increase scroll speed
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
scroll_speed = 140.0
font_px = 28
beat_color_enabled = true
alpha = 0.88
```

## Runtime Persistence

State is persisted to the app's shared runtime state file (`banner` key in
`runtime/global_state.json`). Banner settings survive restarts automatically.

## Structured Docs

- docs/operations.md
- docs/configuration.md
- docs/integration.md
- docs/troubleshooting.md
