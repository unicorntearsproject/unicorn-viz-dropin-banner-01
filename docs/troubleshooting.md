# banner-01 Troubleshooting

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-09-17

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

### Typed text stops accepting characters partway through a line

- Expected: each line is hard-capped at `max_line_chars` (100 by default)
  while typing. Press `Enter`/`Shift+Enter` to continue on a new line, or
  raise the cap with `Right` in the config modal (not editing).

## Related Docs

- ../README.md
- operations.md
- configuration.md
- integration.md
