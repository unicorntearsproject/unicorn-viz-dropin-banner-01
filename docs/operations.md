# banner-01 Operations

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-09-17

## Purpose

Operational runbook for the banner subsystem during live sessions.

## Startup Checklist

1. Confirm drop-in is present under `drop-ins/banner-01/`.
2. Confirm `[banner] enabled = true` in config when startup visibility is desired.
3. Start Unicorn Viz and press `Shift+B` to verify runtime toggle.
4. Open config with `Ctrl+B` and confirm text editor interaction.

## Live Controls

- `Shift+B`: toggle banner strip
- `Ctrl+B`: open/close config
- `Enter`: toggle edit mode
- `Shift+Enter`: insert newline
- `Left`/`Right`: decrease/increase max line length (chars)
- `Esc`: close config modal

## Runtime Notes

- Banner renders as a subsystem overlay and is independent from effect shaders.
- One line is shown at a time, fading in/out over 1s with a hold in between,
  then advancing to the next line -- blank lines are skipped.
- Text animation (drippy vertical wave) and color both react to bass/beat;
  neither depends on frame-perfect timing the way the old scroll did, so a
  dropped frame doesn't read as a stutter.
- In multi-head span/mirror modes, banner targets the primary overlay viewport.
- Edit mode owns plain text keys to avoid accidental global hotkey firing.

## Related Docs

- ../README.md
- configuration.md
- integration.md
- troubleshooting.md
