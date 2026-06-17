# banner-01 Integration

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-06-16

## Purpose

Describe how banner-01 integrates with runtime hotkey dispatch, overlay rendering,
and shared state.

## Integration Points

- Loaded as a subsystem runtime capability (`banner`).
- Registers key handler via VJ API runtime registration.
- Exposes `render_overlay(width, height, dt, audio)` for frame-stage rendering.
- Persists state via `vj_api.set_runtime_state('banner', payload)`.

## Rendering Placement

- Banner renders as a post-effect subsystem overlay.
- In single-display mode, it uses the full render canvas.
- In multi-head span/mirror modes, it resolves and uses app primary overlay
  viewport routing for monitor-correct anchoring.

## Key Handling

- Banner consumes editing keys while config/edit mode is active.
- During config mode, plain `U` is owned by banner to avoid splash replay hotkey
  conflicts from other runtime controllers.

## Related Docs

- ../README.md
- operations.md
- configuration.md
- troubleshooting.md
