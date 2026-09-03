# banner-01 Troubleshooting

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-09-24

## Purpose

Troubleshooting guide for banner rendering, editing, and persistence behavior.

## Common Issues

### Text typing triggers global hotkeys

- Ensure config modal is open (`Ctrl+B`) and edit mode is enabled (`Enter`).
- Verify latest `banner-01` and `unicorn-tears-01` are pulled if `U` triggers
  splash instead of text input.

### Banner appears on wrong monitor in span/mirror modes

- Verify active display mode and configured primary display index.
- Confirm multi-head drop-in is loaded and reporting expected topology.
- Verify latest `banner-01` with primary overlay viewport anchoring support.

### Banner text resets after restart

- Confirm runtime state save is enabled in app shutdown path.
- Verify `runtime/global_state.json` file is writable.

### Config modal clips text

- Use shorter lines or newline breaks.
- Reduce `font_px` with Down arrow while config is open.
- A too-long line scrolls horizontally to keep the cursor visible rather
  than wrapping (0.10.0+) -- this is expected, not clipping.

### Typed text stops accepting characters partway through a line

- Expected: each line is hard-capped at `max_line_chars` (100 by default)
  while typing. The field's border flashes red for an instant when this
  happens (0.10.0+) -- press `Enter`/`Shift+Enter` to continue on a new
  line, or raise the cap with `Right` in the config modal (not editing).

### Holding Backspace/Delete/an arrow key doesn't repeat

- Fixed in core (unrelated to the `banner-01` version) -- `unicornviz/
  app.py`'s key dispatch drops all `SDL_KEYDOWN` auto-repeat by design
  (so holding an ordinary hotkey doesn't repeat-fire it), but now lets
  repeats through whenever some drop-in's text editor has focus. Verify
  core is current if this still doesn't repeat.

### Selection doesn't seem to do anything

- Before 0.10.0, Shift+move updated the internal selection but nothing
  drew it -- selecting genuinely worked, it just wasn't visible. Now
  highlighted; verify latest `banner-01` if it still isn't.

## Related Docs

- ../README.md
- operations.md
- configuration.md
- integration.md
