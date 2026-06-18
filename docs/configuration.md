# banner-01 Configuration

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-06-16

## Purpose

Reference for banner drop-in configuration keys.

## Config Block

```toml
[banner]
enabled = true
text = "Welcome to Unicorn Viz"
scroll_speed = 140.0
font_px = 28
beat_color_enabled = true
alpha = 0.88
```

## Keys

- `enabled` (bool): startup visibility state.
- `text` (str): marquee content.
- `scroll_speed` (float): horizontal movement speed in pixels/second.
- `font_px` (int): font size in pixels.
- `beat_color_enabled` (bool): enable beat-reactive text color cycling.
- `alpha` (float): strip opacity.

## Valid Ranges

- `scroll_speed`: clamped to `40.0` - `420.0`
- `font_px`: clamped to `16` - `56`
- `alpha`: clamped to `0.35` - `1.0`

## Related Docs

- ../README.md
- operations.md
- integration.md
- troubleshooting.md
