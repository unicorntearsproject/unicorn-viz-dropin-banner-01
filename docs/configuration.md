# banner-01 Configuration

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-09-17

## Purpose

Reference for banner drop-in configuration keys.

## Config Block

```toml
[banner]
enabled = true
text = "Welcome to Unicorn Viz"
max_line_chars = 100
font_px = 28
beat_color_enabled = true
alpha = 0.88
```

## Keys

- `enabled` (bool): startup visibility state.
- `text` (str): banner content. `\n` starts a new line in the fade cycle;
  one line is shown at a time. Lines longer than `max_line_chars` are
  word-wrapped to fit at load time, same as while typing.
- `max_line_chars` (int): hard character cap per line, enforced live while
  typing in the config modal's editor.
- `font_px` (int): font size in pixels.
- `beat_color_enabled` (bool): enable beat-reactive text color cycling.
- `alpha` (float): strip opacity.

## Valid Ranges

- `max_line_chars`: clamped to `20` - `200`
- `font_px`: clamped to `16` - `56`
- `alpha`: clamped to `0.35` - `1.0`

## Related Docs

- ../README.md
- operations.md
- integration.md
- troubleshooting.md
