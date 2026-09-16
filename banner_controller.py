"""Bottom-edge marquee banner overlay drop-in.

Provides a stream-style bottom-edge banner that can be toggled and configured
at runtime without modifying core overlay internals.

0.9.0 replaced continuous horizontal scrolling with a multi-line, fade-cycled
display: side-scrolling depended on a smooth, uninterrupted per-frame dt to
track a pixel offset, which reads as visibly jittery under the same variable
frame pacing a stream encode or CPU contention already produces elsewhere.
Fading between whole, fixed lines has no such continuity requirement -- a
dropped or delayed frame just means the fade fraction is slightly behind
where it "should" be, not a visible stutter in position.

Text editing uses SDL_TEXTINPUT events (via VJApi.register_text_input_handler)
and behaves like an ordinary textarea: full cursor navigation (arrow keys,
Home/End/Up/Down scoped to the current logical line, Ctrl+Home/End for the
whole text, Ctrl+Arrow word-jump), a visible Shift-selection highlight,
Ctrl+A/C/X/V, and Backspace/Delete/Ctrl+Backspace. One row on screen per
logical line -- max_line_chars is the editable unit, not a separate
pixel-wrapped display layer -- with horizontal scroll-follow instead of
wrapping a too-long row.
"""
from __future__ import annotations

__version__ = '0.13.1'

import logging
import math
import textwrap
from dataclasses import dataclass
from typing import Any

import moderngl
import numpy as np
from PIL import Image, ImageDraw, ImageFont


log = logging.getLogger(__name__)

DROPIN_CAPABILITIES = {
    'name': 'banner',
    'class_symbol': 'BannerController',
    'subsystem_name': 'banner',
    'key_handler_attr': 'handle_key',
}

HELP_ENTRIES = [
    ('Banner', 'Shift+B',       'Toggle bottom marquee banner on/off'),
    ('Banner', 'Ctrl+B',        'Toggle banner config modal'),
    ('Banner', 'Alt+D',         'Toggle drippy wave animation (config modal open)'),
    ('Banner', 'Ctrl+R',        'Reset banner text and max line length to default'),
    ('Banner', 'Arrows',        'Adjust max line length/font while config modal is open'),
    ('Banner', 'Enter',         'Toggle live text edit mode in config modal'),
    ('Banner', '↑↓←→ Home/End', 'Move cursor while editing (Home/End: current line)'),
    ('Banner', 'Ctrl+Home/End', 'Jump to start/end of all text while editing'),
    ('Banner', 'Ctrl+←→',      'Jump word while editing'),
    ('Banner', 'Shift + move',  'Select text while editing'),
    ('Banner', 'Ctrl+A/C/X/V',  'Select all / copy / cut / paste while editing'),
]

# Fade timing for the multi-line cycle -- fixed, not config-exposed (unlike
# max_line_chars, which was the one explicitly asked to be tunable).
_FADE_S = 1.0
_LINE_HOLD_S = 2.6

_DEFAULT_MAX_LINE_CHARS = 100
_MIN_LINE_CHARS = 20
_MAX_LINE_CHARS = 200

# Quantization for the render cache in render_overlay(): the fade fraction
# and beat-driven text color are continuous every frame, so a raw equality
# check would never hit. Collapsing them into coarse, visually-imperceptible
# buckets means a held or slowly-fading line reuses one rasterized banner
# across many frames instead of rebuilding on every one.
_FADE_QUANT_STEPS = 48
_COLOR_QUANT_STEP = 8

# Flat prose, reflowed to the active max_line_chars by _wrap_to_chars() --
# see _build_default_text() -- rather than hardcoded with line breaks, so a
# lowered/raised max_line_chars still produces a sensible default on reset.
_DEFAULT_TEXT_RAW = (
    'You are watching the Unicorn Tears Stream brought to you by DJ Tears, '
    'visualization by Unicorn-Viz.  Follow DJ Unicorn Tears on all your '
    'favorite platforms!  Visit UnicornTears.com to download Unicorn-Viz for '
    'free!  Please help us grow - like, share & Follow!  Monthly subscribers '
    'get early access to music, videos & software!'
)


@dataclass(slots=True)
class _TextAreaLayout:
    """Simple reusable text-area layout descriptor."""

    x: int
    y: int
    w: int
    h: int
    padding: int = 10
    lines: int = 4


class BannerController:
    """Render a configurable bottom-edge, fade-cycled banner strip over all effects.

    Text editing uses SDL_TEXTINPUT events registered via VJApi so character
    input is handled correctly regardless of keyboard layout, dead keys, or IME.
    While edit mode is active the controller registers itself as a text input
    handler and calls SDL_StartTextInput; both are cleaned up on close.
    """

    HELP_ENTRIES = HELP_ENTRIES

    def captures_global_hotkeys(self) -> bool:
        """Return True when banner UI should own plain key input."""
        return bool(self._show_config)

    def __init__(self, app: Any, cfg: dict[str, Any] | None = None) -> None:
        self._app = app
        self._cfg = dict(cfg or {})
        self._ctx: moderngl.Context = app.vj_api.ctx
        self._vj_api = app.vj_api

        # Off at boot unless the config says otherwise (owner decision from
        # the 2026-09-14 Windows beta: the marquee greeted a fresh install).
        self._enabled = bool(self._cfg.get('enabled', False))
        self._show_config = False
        self._edit_mode = False

        self._max_line_chars = int(self._cfg.get('max_line_chars', _DEFAULT_MAX_LINE_CHARS) or _DEFAULT_MAX_LINE_CHARS)
        self._max_line_chars = max(_MIN_LINE_CHARS, min(_MAX_LINE_CHARS, self._max_line_chars))

        self._text = str(self._cfg.get('text', '') or '').strip()
        if not self._text:
            self._text = self._build_default_text()
        self._text = self._enforce_line_cap(self._text)

        self._font_px = int(self._cfg.get('font_px', 28) or 28)
        self._beat_color_enabled = bool(self._cfg.get('beat_color_enabled', True))
        # Off by default -- the per-character wave/sag is also the one thing
        # that forces a full rebuild every frame (see _banner_cache_key in
        # render_overlay()); most operators want the static, cheap banner.
        self._drip_enabled = bool(self._cfg.get('drip_enabled', False))
        self._alpha = float(self._cfg.get('alpha', 0.88) or 0.88)

        self._font_px = max(16, min(56, self._font_px))
        self._alpha = max(0.35, min(1.0, self._alpha))

        self._pad_x = 14
        self._pad_y = 6
        self._color_phase = 0.0
        self._drip_phase = 0.0

        # Multi-line fade cycle: one line visible at a time, fading in,
        # holding, fading out, then advancing -- see _current_line_fade().
        self._line_idx = 0
        self._line_cycle_t = 0.0

        # Brief red border flash when a keystroke is rejected for being at
        # the per-line cap -- otherwise hitting the cap is silent and reads
        # as the editor randomly dropping input. Decayed in update().
        self._cap_flash_t = 0.0

        self._bass = 0.0
        self._mid = 0.0
        self._treble = 0.0
        self._beat = 0.0

        # Cursor / selection state for text edit mode
        self._cursor: int = len(self._text)
        self._sel_start: int | None = None

        # Rasterized-banner cache -- see render_overlay(). None whenever the
        # drip animation is on (it changes every frame by design, so there's
        # nothing to cache); otherwise the key the current GL texture's
        # content was built from, so an unchanged frame skips both the PIL
        # rasterize and the texture upload entirely.
        self._banner_cache_key: tuple | None = None

        self._font = self._load_font(self._font_px)
        self._banner_height = self._compute_banner_height()

        self._program = self._ctx.program(
            vertex_shader='''
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
''',
            fragment_shader='''
#version 330
uniform sampler2D tex;
uniform vec4 tint;
in vec2 v_uv;
out vec4 fragColor;
void main() {
    vec4 s = texture(tex, v_uv);
    fragColor = vec4(s.rgb * tint.rgb, s.a * tint.a);
}
''',
        )

        self._quad_vbo = self._ctx.buffer(reserve=6 * 4 * 4)
        self._quad_vao = self._ctx.vertex_array(
            self._program,
            [(self._quad_vbo, '2f 2f', 'in_pos', 'in_uv')],
        )

        self._banner_tex: moderngl.Texture | None = None
        self._banner_size = (0, 0)
        self._modal_tex: moderngl.Texture | None = None
        self._modal_size = (0, 0)
        self._white_tex = self._ctx.texture((1, 1), 4, data=bytes([255, 255, 255, 255]))
        self._white_tex.filter = moderngl.LINEAR, moderngl.LINEAR

        self._restore_runtime_state()

        try:
            vj = self._vj_api
            vj.register_midi_actions('Banner', [
                ('banner_toggle', 'Toggle scroll banner'),
            ])
            vj.register_midi_action_handler('banner_toggle', self._midi_toggle)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).debug('banner-01: MIDI registration failed: %s', exc)
        # Control Room panel (docs/planning/control-room-drop-in-integration-
        # plan-2026-09-05.md, P1); guarded so an older core just gets no panel.
        register = getattr(self._vj_api, 'register_operator_panel', None)
        if callable(register):
            try:
                from unicornviz.operator_panels import OperatorPanel  # noqa: PLC0415
                register(OperatorPanel(
                    name='banner',
                    title='Banner',
                    page='overlays',
                    priority=310,
                    size='small',
                    content=self._operator_panel_content,
                    on_action=self._operator_panel_action,
                    tooltips={
                        'toggle': 'Show / hide the bottom banner',
                        'edit': 'Open the banner config modal on the audience window',
                        'reset': 'Reset banner text and max line length to default',
                    },
                ))
            except Exception as exc:
                import logging  # noqa: PLC0415
                logging.getLogger(__name__).debug('banner-01: operator panel registration failed: %s', exc)

    def _operator_panel_content(self) -> Any:
        """Control Room panel state -- CR render thread; attribute reads only."""
        from unicornviz.operator_panels import PanelButton, PanelContent, PanelRow  # noqa: PLC0415
        on = bool(self._enabled)
        return PanelContent(
            status='ON' if on else 'OFF',
            rows=(
                PanelRow('Lines', str(len(self._display_lines()))),
                PanelRow('Max line', f'{self._max_line_chars}ch'),
                PanelRow('Font', f'{self._font_px}px'),
                PanelRow('Beat color', 'on' if self._beat_color_enabled else 'off'),
            ),
            buttons=(
                PanelButton('toggle', 'TOGGLE', active=on),
                PanelButton('edit', 'EDIT', active=self._show_config),
                PanelButton('reset', 'RESET', accent='warn'),
            ),
        )

    def _operator_panel_action(self, action: str, _payload: Any) -> str | None:
        """Control Room buttons -> the same state changes and flash text as the hotkeys."""
        if action == 'toggle':
            self._enabled = not self._enabled
            self._persist_runtime_state()
            return f'Banner: {"ON" if self._enabled else "OFF"}'
        if action == 'edit':
            self._show_config = True
            return 'Banner config: ON'
        if action == 'reset':
            self._max_line_chars = _DEFAULT_MAX_LINE_CHARS
            self._text = self._build_default_text()
            self._cursor = len(self._text)
            self._sel_start = None
            self._persist_runtime_state()
            return 'Banner text + max line reset'
        return None

    def _midi_toggle(self) -> None:
        """MIDI handler: toggle the banner on/off."""
        self._enabled = not self._enabled

    # ── Text field operations ────────────────────────────────────────────────

    def _tf_sel_range(self) -> tuple[int, int] | None:
        if self._sel_start is None or self._sel_start == self._cursor:
            return None
        return (min(self._sel_start, self._cursor), max(self._sel_start, self._cursor))

    def _tf_set_cursor(self, pos: int, select: bool = False) -> None:
        pos = max(0, min(len(self._text), pos))
        if select:
            if self._sel_start is None:
                self._sel_start = self._cursor
        else:
            self._sel_start = None
        self._cursor = pos

    def _tf_delete_selection(self) -> bool:
        r = self._tf_sel_range()
        if r is None:
            return False
        lo, hi = r
        self._text = self._text[:lo] + self._text[hi:]
        self._cursor = lo
        self._sel_start = None
        return True

    def _tf_insert(self, text: str) -> None:
        self._tf_delete_selection()
        clean = ''.join(ch for ch in text if ch >= ' ' or ch in ('\t', '\n'))
        if not clean:
            return
        p = self._cursor

        # Hard-cap each line at max_line_chars as the operator types: a
        # newline always goes through (it starts a fresh line), any other
        # character is dropped once the line it would land on is already
        # at the cap -- Enter/Shift+Enter is how you continue past it.
        line_start = self._text.rfind('\n', 0, p) + 1
        line_end = self._text.find('\n', p)
        if line_end == -1:
            line_end = len(self._text)
        remainder_len = line_end - p
        line_len = line_end - line_start
        capped: list[str] = []
        rejected = False
        for ch in clean:
            if ch == '\n':
                capped.append(ch)
                line_len = remainder_len
                continue
            if line_len >= self._max_line_chars:
                rejected = True
                continue
            capped.append(ch)
            line_len += 1
        if rejected:
            self._cap_flash_t = 0.35
        result = ''.join(capped)
        if not result:
            return

        self._text = self._text[:p] + result + self._text[p:]
        self._cursor = p + len(result)

    def _tf_delete_before(self) -> None:
        if self._tf_delete_selection():
            return
        if self._cursor > 0:
            self._text = self._text[:self._cursor - 1] + self._text[self._cursor:]
            self._cursor -= 1

    def _tf_delete_after(self) -> None:
        if self._tf_delete_selection():
            return
        if self._cursor < len(self._text):
            self._text = self._text[:self._cursor] + self._text[self._cursor + 1:]

    def _tf_delete_word_before(self) -> None:
        old = self._cursor
        self._tf_move_word_left()
        if self._cursor < old:
            self._text = self._text[:self._cursor] + self._text[old:]

    def _tf_move_left(self, select: bool = False) -> None:
        if not select and self._tf_sel_range():
            self._tf_set_cursor(self._tf_sel_range()[0])
        else:
            self._tf_set_cursor(self._cursor - 1, select)

    def _tf_move_right(self, select: bool = False) -> None:
        if not select and self._tf_sel_range():
            self._tf_set_cursor(self._tf_sel_range()[1])
        else:
            self._tf_set_cursor(self._cursor + 1, select)

    def _tf_move_home(self, select: bool = False) -> None:
        """Move to the start of the current logical (real-\\n-delimited)
        line -- that's the editable unit now, one row on screen per line."""
        line_start = self._text.rfind('\n', 0, self._cursor) + 1
        self._tf_set_cursor(line_start, select)

    def _tf_move_end(self, select: bool = False) -> None:
        line_end = self._text.find('\n', self._cursor)
        if line_end == -1:
            line_end = len(self._text)
        self._tf_set_cursor(line_end, select)

    def _tf_move_doc_home(self, select: bool = False) -> None:
        self._tf_set_cursor(0, select)

    def _tf_move_doc_end(self, select: bool = False) -> None:
        self._tf_set_cursor(len(self._text), select)

    def _tf_move_up(self, select: bool = False) -> None:
        line_start = self._text.rfind('\n', 0, self._cursor) + 1
        col = self._cursor - line_start
        if line_start == 0:
            self._tf_set_cursor(0, select)
            return
        prev_end = line_start - 1   # the '\n' that separates this line from the prior one
        prev_start = self._text.rfind('\n', 0, prev_end) + 1
        self._tf_set_cursor(prev_start + min(col, prev_end - prev_start), select)

    def _tf_move_down(self, select: bool = False) -> None:
        line_start = self._text.rfind('\n', 0, self._cursor) + 1
        col = self._cursor - line_start
        line_end = self._text.find('\n', self._cursor)
        if line_end == -1:
            self._tf_set_cursor(len(self._text), select)
            return
        next_start = line_end + 1
        next_end = self._text.find('\n', next_start)
        if next_end == -1:
            next_end = len(self._text)
        self._tf_set_cursor(next_start + min(col, next_end - next_start), select)

    def _tf_move_word_left(self, select: bool = False) -> None:
        pos = self._cursor
        while pos > 0 and self._text[pos - 1] in (' ', '\n'):
            pos -= 1
        while pos > 0 and self._text[pos - 1] not in (' ', '\n'):
            pos -= 1
        self._tf_set_cursor(pos, select)

    def _tf_move_word_right(self, select: bool = False) -> None:
        pos = self._cursor
        n = len(self._text)
        while pos < n and self._text[pos] not in (' ', '\n'):
            pos += 1
        while pos < n and self._text[pos] in (' ', '\n'):
            pos += 1
        self._tf_set_cursor(pos, select)

    def _tf_select_all(self) -> None:
        self._sel_start = 0
        self._cursor = len(self._text)

    def _tf_selected_text(self) -> str:
        r = self._tf_sel_range()
        if r is None:
            return ''
        lo, hi = r
        return self._text[lo:hi]

    def _clipboard_get(self) -> str:
        import sdl2  # noqa: PLC0415
        try:
            raw = sdl2.SDL_GetClipboardText()
        except Exception:
            return ''
        if not raw:
            return ''
        return raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)

    def _clipboard_set(self, text: str) -> None:
        import sdl2  # noqa: PLC0415
        try:
            sdl2.SDL_SetClipboardText(text.encode('utf-8'))
        except Exception:
            pass

    # ── Edit mode lifecycle ──────────────────────────────────────────────────

    def _enter_edit_mode(self) -> None:
        if self._edit_mode:
            return
        self._edit_mode = True
        self._cursor = len(self._text)
        self._sel_start = None
        self._vj_api.register_text_input_handler('banner', self._on_text_input)
        self._vj_api.start_text_input()

    def _exit_edit_mode(self) -> None:
        if not self._edit_mode:
            return
        self._edit_mode = False
        self._vj_api.unregister_text_input_handler('banner')
        self._vj_api.stop_text_input()
        self._persist_runtime_state()

    def _on_text_input(self, text: str) -> None:
        if not self._edit_mode:
            return
        self._tf_insert(text)
        self._persist_runtime_state()

    # ── Config / runtime state ────────────────────────────────────────────────

    def _apply_state_payload(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        self._max_line_chars = max(
            _MIN_LINE_CHARS,
            min(_MAX_LINE_CHARS, int(raw.get('max_line_chars', self._max_line_chars) or self._max_line_chars)),
        )
        text = str(raw.get('text', self._text) or self._text).strip()
        if text:
            self._text = text
        self._text = self._enforce_line_cap(self._text)
        self._enabled = bool(raw.get('enabled', self._enabled))
        self._font_px = max(16, min(56, int(raw.get('font_px', self._font_px) or self._font_px)))
        self._beat_color_enabled = bool(raw.get('beat_color_enabled', self._beat_color_enabled))
        self._drip_enabled = bool(raw.get('drip_enabled', self._drip_enabled))
        self._alpha = max(
            0.35,
            min(1.0, float(raw.get('alpha', self._alpha) or self._alpha)),
        )
        self._font = self._load_font(self._font_px)
        self._banner_height = self._compute_banner_height()

    def _wrap_to_chars(self, text: str, width: int) -> list[str]:
        """Word-wrap ``text`` to ``width`` characters per line (character
        count, not pixel measurement -- max_line_chars is a character cap)."""
        wrapped = textwrap.wrap(
            text, width=max(1, width), break_long_words=True, break_on_hyphens=False,
        )
        return wrapped or ['']

    def _build_default_text(self) -> str:
        """Reflow the flat default prose to the current max_line_chars."""
        return '\n'.join(self._wrap_to_chars(_DEFAULT_TEXT_RAW, self._max_line_chars))

    def _enforce_line_cap(self, text: str) -> str:
        """Rewrap any line in ``text`` exceeding max_line_chars.

        Reflows rather than truncates so a lowered max_line_chars (or
        config/state loaded from before this line got longer) never
        silently drops content -- only live typing hard-caps (see
        _tf_insert()), where the operator sees the cap in the moment.
        """
        out: list[str] = []
        for line in text.split('\n'):
            if len(line) <= self._max_line_chars:
                out.append(line)
            else:
                out.extend(self._wrap_to_chars(line, self._max_line_chars))
        return '\n'.join(out)

    def _display_lines(self) -> list[str]:
        """Return the non-blank lines that actually get displayed."""
        return [ln for ln in self._text.split('\n') if ln.strip()]

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        # Shared resolver: bundled ui-font.ttf first, then per-platform
        # system dirs (incl. Windows) — see unicornviz/fonts.py.
        from unicornviz.fonts import load_font
        return load_font(size)

    def _compute_banner_height(self) -> int:
        test = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
        draw = ImageDraw.Draw(test)
        bbox = draw.textbbox((0, 0), 'Ag', font=self._font)
        text_h = max(16, int(bbox[3] - bbox[1]))
        return text_h + self._pad_y * 2 + 2

    def _persist_runtime_state(self) -> None:
        payload = {
            'enabled': bool(self._enabled),
            'text': str(self._text),
            'max_line_chars': int(self._max_line_chars),
            'font_px': int(self._font_px),
            'beat_color_enabled': bool(self._beat_color_enabled),
            'drip_enabled': bool(self._drip_enabled),
            'alpha': float(self._alpha),
        }
        self._vj_api.set_runtime_state('banner', payload)

    def _restore_runtime_state(self) -> None:
        self._apply_state_payload(self._vj_api.get_runtime_state('banner', default={}))

    def _set_font_px(self, value: int) -> None:
        value = max(16, min(56, int(value)))
        if value == self._font_px:
            return
        self._font_px = value
        self._font = self._load_font(self._font_px)
        self._banner_height = self._compute_banner_height()

    def _set_max_line_chars(self, value: int) -> None:
        value = max(_MIN_LINE_CHARS, min(_MAX_LINE_CHARS, int(value)))
        if value == self._max_line_chars:
            return
        self._max_line_chars = value
        # A lowered cap can leave existing lines over the new limit --
        # reflow now rather than let it linger until the next edit/reload.
        self._text = self._enforce_line_cap(self._text)

    # ── Key handler ──────────────────────────────────────────────────────────

    def handle_key(self, sym: int, mod: int) -> 'str | None | bool':
        import sdl2  # noqa: PLC0415

        ctrl  = bool(mod & sdl2.KMOD_CTRL)
        alt   = bool(mod & sdl2.KMOD_ALT)
        gui   = bool(mod & sdl2.KMOD_GUI)
        shift = bool(mod & sdl2.KMOD_SHIFT)

        # Special-case U while config is open so replay-splash never steals it.
        if self._show_config and sym == sdl2.SDLK_u and not (ctrl or alt or gui):
            # In edit mode, 'U' is handled via SDL_TEXTINPUT; just block it here.
            return True

        # ── Edit mode: full cursor navigation ────────────────────────────────
        if self._show_config and self._edit_mode:
            if sym == sdl2.SDLK_ESCAPE:
                self._exit_edit_mode()
                self._show_config = False
                return 'Banner config: OFF'

            if sym in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                if shift:
                    self._tf_insert('\n')
                    self._persist_runtime_state()
                    return None
                self._exit_edit_mode()
                return 'Banner text edit: OFF'

            if sym == sdl2.SDLK_LEFT:
                self._tf_move_word_left(select=shift) if ctrl else self._tf_move_left(select=shift)
                return None

            if sym == sdl2.SDLK_RIGHT:
                self._tf_move_word_right(select=shift) if ctrl else self._tf_move_right(select=shift)
                return None

            if sym == sdl2.SDLK_UP:
                self._tf_move_up(select=shift)
                return None

            if sym == sdl2.SDLK_DOWN:
                self._tf_move_down(select=shift)
                return None

            if sym == sdl2.SDLK_HOME:
                self._tf_move_doc_home(select=shift) if ctrl else self._tf_move_home(select=shift)
                return None

            if sym == sdl2.SDLK_END:
                self._tf_move_doc_end(select=shift) if ctrl else self._tf_move_end(select=shift)
                return None

            if sym == sdl2.SDLK_BACKSPACE:
                self._tf_delete_word_before() if ctrl else self._tf_delete_before()
                self._persist_runtime_state()
                return None

            if sym == sdl2.SDLK_DELETE:
                self._tf_delete_after()
                self._persist_runtime_state()
                return None

            if sym == sdl2.SDLK_a and ctrl:
                self._tf_select_all()
                return None

            if sym == sdl2.SDLK_c and ctrl:
                selected = self._tf_selected_text()
                if selected:
                    self._clipboard_set(selected)
                return None

            if sym == sdl2.SDLK_x and ctrl:
                selected = self._tf_selected_text()
                if selected:
                    self._clipboard_set(selected)
                    self._tf_delete_selection()
                    self._persist_runtime_state()
                return None

            if sym == sdl2.SDLK_v and ctrl:
                pasted = self._clipboard_get()
                if pasted:
                    self._tf_insert(pasted)
                    self._persist_runtime_state()
                return None

            if sym == sdl2.SDLK_r and ctrl:
                self._max_line_chars = _DEFAULT_MAX_LINE_CHARS
                self._text = self._build_default_text()
                self._cursor = len(self._text)
                self._sel_start = None
                self._persist_runtime_state()
                return 'Banner text + max line reset'

            # Consume everything else (including ctrl/alt combos) while editing
            # so character keys delivered via SDL_TEXTINPUT are not double-fired.
            return True

        # ── Config modal: navigation only (no editing) ────────────────────────
        if sym == sdl2.SDLK_b and ctrl and not alt and not gui:
            self._show_config = not self._show_config
            if not self._show_config:
                self._exit_edit_mode()
            return f'Banner config: {"ON" if self._show_config else "OFF"}'

        if sym == sdl2.SDLK_b and shift and not ctrl and not alt and not gui:
            self._enabled = not self._enabled
            self._persist_runtime_state()
            return f'Banner: {"ON" if self._enabled else "OFF"}'

        if not self._show_config:
            return False

        if sym == sdl2.SDLK_ESCAPE:
            self._exit_edit_mode()
            self._show_config = False
            self._persist_runtime_state()
            return 'Banner config: OFF'

        if sym in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
            if not self._edit_mode:
                self._enter_edit_mode()
                return 'Banner text edit: ON'
            self._exit_edit_mode()
            return 'Banner text edit: OFF'

        if sym == sdl2.SDLK_LEFT:
            self._set_max_line_chars(self._max_line_chars - 10)
            self._persist_runtime_state()
            return f'Banner max line: {self._max_line_chars}ch'
        if sym == sdl2.SDLK_RIGHT:
            self._set_max_line_chars(self._max_line_chars + 10)
            self._persist_runtime_state()
            return f'Banner max line: {self._max_line_chars}ch'
        if sym == sdl2.SDLK_UP:
            self._set_font_px(self._font_px + 1)
            self._persist_runtime_state()
            return f'Banner font: {self._font_px}px'
        if sym == sdl2.SDLK_DOWN:
            self._set_font_px(self._font_px - 1)
            self._persist_runtime_state()
            return f'Banner font: {self._font_px}px'
        if sym == sdl2.SDLK_b and alt and not ctrl and not shift and not gui:
            self._beat_color_enabled = not self._beat_color_enabled
            self._persist_runtime_state()
            return f'Banner beat color: {"ON" if self._beat_color_enabled else "OFF"}'
        if sym == sdl2.SDLK_d and alt and not ctrl and not shift and not gui:
            self._drip_enabled = not self._drip_enabled
            self._persist_runtime_state()
            return f'Banner drip: {"ON" if self._drip_enabled else "OFF"}'
        if sym == sdl2.SDLK_r and ctrl:
            self._max_line_chars = _DEFAULT_MAX_LINE_CHARS
            self._text = self._build_default_text()
            self._cursor = len(self._text)
            self._sel_start = None
            self._persist_runtime_state()
            return 'Banner text + max line reset'

        return True

    # ── Audio + scroll ────────────────────────────────────────────────────────

    def update(self, dt: float, audio: Any) -> None:
        self._bass   = float(getattr(audio, 'bass_n',   0.0) or 0.0)
        self._mid    = float(getattr(audio, 'mid_n',    0.0) or 0.0)
        self._treble = float(getattr(audio, 'treble_n', 0.0) or 0.0)
        self._beat   = float(getattr(audio, 'beat',     0.0) or 0.0)

        dt = max(0.0, float(dt))
        if self._enabled:
            lines = self._display_lines()
            if lines:
                self._line_idx %= len(lines)
                cycle_len = _FADE_S * 2.0 + _LINE_HOLD_S
                self._line_cycle_t += dt
                if self._line_cycle_t >= cycle_len:
                    self._line_cycle_t -= cycle_len
                    self._line_idx = (self._line_idx + 1) % len(lines)
        self._color_phase += dt * (0.55 + self._mid * 1.4)
        # Drippy per-character wave speed rides bass/beat -- a quiet passage
        # barely moves, a hit makes it visibly ooze.
        self._drip_phase += dt * (1.15 + self._bass * 2.4 + self._beat * 1.6)
        if self._cap_flash_t > 0.0:
            self._cap_flash_t = max(0.0, self._cap_flash_t - dt)

    def _current_line_fade(self) -> tuple[str, float]:
        """Return (line_text, fade_fraction in [0, 1]) for the active line."""
        lines = self._display_lines()
        if not lines:
            return '', 0.0
        idx = self._line_idx % len(lines)
        t = self._line_cycle_t
        if t < _FADE_S:
            frac = t / _FADE_S
        elif t < _FADE_S + _LINE_HOLD_S:
            frac = 1.0
        else:
            frac = 1.0 - (t - _FADE_S - _LINE_HOLD_S) / _FADE_S
        frac = max(0.0, min(1.0, frac))
        # Smoothstep -- a gentler ease than a linear ramp for a 1s fade.
        frac = frac * frac * (3.0 - 2.0 * frac)
        return lines[idx], frac

    def _current_text_color(self) -> tuple[int, int, int, int]:
        if not self._beat_color_enabled:
            return (234, 238, 245, 255)

        pulse = min(1.0, self._beat + self._treble * 0.55)
        r = 150 + int(90 * (0.5 + 0.5 * math.sin(self._color_phase + 0.0)))
        g = 150 + int(90 * (0.5 + 0.5 * math.sin(self._color_phase + 2.1)))
        b = 150 + int(90 * (0.5 + 0.5 * math.sin(self._color_phase + 4.2)))
        lift = int(40 * pulse)
        return (
            max(90, min(255, r + lift)),
            max(90, min(255, g + lift)),
            max(90, min(255, b + lift)),
            255,
        )

    def _quantized_fade(self, fade: float) -> int:
        return round(max(0.0, min(1.0, fade)) * _FADE_QUANT_STEPS)

    def _quantized_color(self, color: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(v - (v % _COLOR_QUANT_STEP) for v in color)

    # ── GL texture helpers ────────────────────────────────────────────────────

    def _ensure_texture(self, tex: moderngl.Texture | None, size: tuple[int, int]) -> moderngl.Texture:
        w, h = size
        if tex is None or tex.size != size:
            if tex is not None:
                tex.release()
            tex = self._ctx.texture(size, 4)
            tex.filter = moderngl.LINEAR, moderngl.LINEAR
        return tex

    def _draw_texture(
        self,
        tex: moderngl.Texture,
        x: float, y: float, w: float, h: float,
        canvas_w: int, canvas_h: int,
        tint: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    ) -> None:
        def px(val: float) -> float:
            return (val / float(canvas_w)) * 2.0 - 1.0

        def py(val: float) -> float:
            return 1.0 - (val / float(canvas_h)) * 2.0

        x0, x1 = px(x), px(x + w)
        y0, y1 = py(y), py(y + h)
        verts = np.array([
            x0, y0, 0.0, 0.0,
            x1, y0, 1.0, 0.0,
            x0, y1, 0.0, 1.0,
            x1, y0, 1.0, 0.0,
            x1, y1, 1.0, 1.0,
            x0, y1, 0.0, 1.0,
        ], dtype=np.float32)
        self._quad_vbo.write(verts.tobytes())
        tex.use(location=0)
        self._program['tex'].value = 0
        self._program['tint'].value = tint
        self._ctx.enable(moderngl.BLEND)
        self._quad_vao.render(moderngl.TRIANGLES)

    # ── Banner strip rendering ─────────────────────────────────────────────────

    def _draw_drippy_text(
        self,
        draw: 'ImageDraw.ImageDraw',
        x: float, y: float,
        text: str,
        color: tuple[int, int, int, int],
        alpha: float,
    ) -> float:
        """Draw ``text``, character-by-character with a bass/beat-driven
        vertical wave -- a "drippy" bob-and-sag -- when ``self._drip_enabled``,
        or as one static string otherwise (also cheaper: one draw call
        instead of one per character, and it makes the banner cacheable
        across frames -- see ``_banner_cache_key`` in ``render_overlay()``).

        Returns the total drawn width (for centering by the caller).
        """
        if alpha <= 0.0 or not text:
            return 0.0
        a = max(0, min(255, int(color[3] * alpha)))
        col = (color[0], color[1], color[2], a)

        if not self._drip_enabled:
            draw.text((x, y), text, font=self._font, fill=col)
            return draw.textlength(text, font=self._font)

        # Base wobble is always faintly alive; bass widens the wave, beat
        # adds an extra downward sag per character -- the "drip".
        wave_amp = 1.4 + self._bass * 5.5
        sag_amp = self._beat * 6.5

        cx = x
        for i, ch in enumerate(text):
            cw = draw.textlength(ch, font=self._font)
            wave = math.sin(self._drip_phase + i * 0.55) * wave_amp
            sag = sag_amp * (0.5 + 0.5 * math.sin(self._drip_phase * 0.31 + i * 0.9))
            draw.text((cx, y + wave + sag), ch, font=self._font, fill=col)
            cx += cw
        return cx - x

    def _build_banner_image(self, width: int) -> Image.Image:
        h = self._banner_height
        img  = Image.new('RGBA', (width, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        bg_alpha = int(255 * self._alpha)
        draw.rectangle((0, 0, width, h), fill=(8, 10, 18, bg_alpha))
        draw.rectangle((0, 0, width, 2), fill=(70, 170, 255, min(255, bg_alpha + 20)))

        line, fade = self._current_line_fade()
        if line and fade > 0.0:
            color = self._current_text_color()
            text_w = draw.textlength(line, font=self._font)
            x = max(float(self._pad_x), (width - text_w) / 2.0)
            y = float(self._pad_y - 1)
            self._draw_drippy_text(draw, x, y, line, color, fade)
        return img

    # ── Config modal rendering ─────────────────────────────────────────────────

    def _build_modal_image(self, width: int) -> tuple[Image.Image, int, int]:
        panel_w = min(width - 40, 1260)
        title_font = self._load_font(max(18, self._font_px - 4))
        body_font  = self._load_font(max(14, self._font_px - 9))
        mono_font  = self._load_font(max(14, self._font_px - 10))

        title_text = 'BANNER CONFIG'
        stats_items = [
            f'Status: {"ON" if self._enabled else "OFF"}',
            f'Max line: {self._max_line_chars}ch',
            f'Font: {self._font_px}px',
            f'Beat Color: {"ON" if self._beat_color_enabled else "OFF"}',
            f'Drip: {"ON" if self._drip_enabled else "OFF"}',
        ]
        edit_state = 'EDITING' if self._edit_mode else 'VIEW'
        edit_color = (255, 214, 110, 255) if self._edit_mode else (176, 196, 220, 255)
        hint_rows: list[list[str]] = [
            [
                'Shift+B: toggle banner', 'Ctrl+B: close config',
                'Alt+B: beat color', 'Alt+D: drip', 'Enter: text edit mode',
            ],
        ]
        if self._edit_mode:
            hint_rows.append(['↑↓←→/Home/End: move', 'Shift+move: select', 'Ctrl+A: select all'])
            hint_rows.append(['Ctrl+C/X/V: copy/cut/paste', 'Shift+Enter: newline', 'Ctrl+R: reset text'])
        else:
            hint_rows.append(['Left/Right: max line length', 'Up/Down: font', 'Ctrl+R: reset text/line length'])
            hint_rows.append(['Enter to start editing text below'])

        def _layout(draw: 'ImageDraw.ImageDraw', dry_run: bool) -> int:
            """Draw (or just measure, if dry_run) the whole modal body
            and return the y just below the last hint row -- the textarea
            starts there. Identical code both passes so they can't drift
            out of sync with each other."""
            if not dry_run:
                draw.text((14, 10), title_text, font=title_font, fill=(170, 220, 255, 255))
                self._draw_config_sprite(draw, panel_w)
            title_w = draw.textlength(title_text, font=title_font)
            stats_x = 14 + title_w + 24
            stats_max_w = panel_w - stats_x - 96  # stay clear of the sprite (80px + margin)
            stats_h = self._draw_wrapped_row(
                draw, int(stats_x), 12, int(stats_max_w), stats_items,
                body_font, (230, 236, 246, 255), dry_run=dry_run,
            )
            y = max(10 + stats_h, 74) + 6  # 74 == sprite bottom (y0=10, size=64)
            hint_max_w = panel_w - 28
            for row in hint_rows:
                y += self._draw_wrapped_row(
                    draw, 14, y, hint_max_w, row,
                    body_font, (184, 204, 224, 255), dry_run=dry_run,
                )
            if not dry_run:
                draw.text((14, y), f'Mode: {edit_state}', font=body_font, fill=edit_color)
            return y + self._line_height(draw, body_font) + 8

        measure_img = Image.new('RGBA', (8, 8))
        measure_draw = ImageDraw.Draw(measure_img)
        content_y = _layout(measure_draw, dry_run=True)

        mono_line_h = self._line_height(measure_draw, mono_font)
        textarea_h = max(110, mono_line_h * 4 + 24)
        panel_h = content_y + textarea_h + 14

        panel = Image.new('RGBA', (panel_w, panel_h), (9, 12, 24, 240))
        draw  = ImageDraw.Draw(panel)
        draw.rectangle((0, 0, panel_w - 1, panel_h - 1), outline=(72, 186, 255, 255), width=2)
        _layout(draw, dry_run=False)

        textarea = _TextAreaLayout(x=14, y=content_y, w=panel_w - 28, h=textarea_h, padding=10, lines=4)
        self._draw_textarea(
            draw=draw,
            font=mono_font,
            layout=textarea,
            value=self._text,
            cursor=self._cursor,
            editing=self._edit_mode,
        )

        return panel, panel_w, panel_h

    def _draw_config_sprite(self, draw: 'ImageDraw.ImageDraw', panel_w: int) -> None:
        sprite_size = 64
        x0 = panel_w - sprite_size - 16
        y0 = 10
        x1 = x0 + sprite_size
        y1 = y0 + sprite_size

        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=8,
            fill=(7, 12, 24, 220),
            outline=(72, 186, 255, 255),
            width=2,
        )

        phase = self._color_phase * 1.8
        for idx in range(6):
            frac = (phase * 0.17 + idx * 0.16) % 1.0
            yy   = y0 + 7 + int(frac * (sprite_size - 14))
            glow = 96 + int(120 * (1.0 - abs(frac - 0.5) * 2.0))
            draw.line(
                (x0 + 8, yy, x1 - 8, yy),
                fill=(30, min(255, glow), min(255, glow + 30), 220),
                width=1,
            )

        cx     = x0 + sprite_size / 2.0 + math.sin(phase * 1.35) * (sprite_size * 0.22)
        cy     = y0 + sprite_size / 2.0 + math.cos(phase * 1.95) * (sprite_size * 0.22)
        radius = 5 + int(2 * min(1.0, self._beat + self._treble * 0.5))
        glow   = (255, 140, 120, 235)
        draw.ellipse(
            (cx - radius - 3, cy - radius - 3, cx + radius + 3, cy + radius + 3),
            fill=(60, 120, 200, 130),
        )
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=glow)

    def _line_height(self, draw: 'ImageDraw.ImageDraw', font: 'ImageFont.ImageFont') -> int:
        bbox = draw.textbbox((0, 0), 'Ag', font=font)
        return max(16, int(bbox[3] - bbox[1]))

    def _draw_wrapped_row(
        self,
        draw: 'ImageDraw.ImageDraw',
        x: int, y: int, max_w: int,
        items: list[str],
        font: 'ImageFont.ImageFont',
        fill: tuple[int, int, int, int],
        dry_run: bool = False,
        gap: int = 24,
    ) -> int:
        """Draw ``items`` left to right starting at ``(x, y)``, wrapping to
        a new line whenever the next item would run past ``x + max_w``.

        Fixed per-item pixel offsets don't adapt to the user's
        configurable ``font_px`` (16-56px, a >3x range): at a larger size
        an item can run into the next one with no visible gap, or the
        whole row can extend past the panel's right edge and render
        off-canvas -- invisible, not just tight. Measuring each item and
        wrapping avoids both. With ``dry_run``, only measures (no actual
        ``draw.text()`` calls) -- used for a first pass that sizes the
        panel before a second, real pass draws into it (same trick as
        ``_compute_banner_height()``: PIL text metrics only need the
        font, not a correctly-sized image).

        Returns the total height drawn, for stacking whatever is next.
        """
        line_h = self._line_height(draw, font) + 8
        cx, cy = x, y
        for item in items:
            w = draw.textlength(item, font=font)
            if cx > x and cx + w > x + max_w:
                cx = x
                cy += line_h
            if not dry_run:
                draw.text((cx, cy), item, font=font, fill=fill)
            cx += w + gap
        return (cy - y) + line_h

    def _fit_end(
        self, draw: 'ImageDraw.ImageDraw', font: 'ImageFont.ImageFont',
        text: str, start: int, row_w: float,
    ) -> int:
        """Largest ``end >= start`` such that ``text[start:end]`` fits ``row_w`` px."""
        n = len(text)
        lo, hi = start, n
        best = start
        while lo <= hi:
            mid = (lo + hi) // 2
            if draw.textlength(text[start:mid], font=font) <= row_w:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def _fit_start(
        self, draw: 'ImageDraw.ImageDraw', font: 'ImageFont.ImageFont',
        text: str, end: int, row_w: float,
    ) -> int:
        """Smallest ``start <= end`` such that ``text[start:end]`` fits ``row_w`` px."""
        lo, hi = 0, end
        best = end
        while lo <= hi:
            mid = (lo + hi) // 2
            if draw.textlength(text[mid:end], font=font) <= row_w:
                best = mid
                hi = mid - 1
            else:
                lo = mid + 1
        return best

    def _row_window(
        self, draw: 'ImageDraw.ImageDraw', font: 'ImageFont.ImageFont',
        line_text: str, row_w: float, cursor_col: int,
    ) -> tuple[int, int]:
        """Char slice of ``line_text`` that fits ``row_w`` px, keeping
        ``cursor_col`` visible -- horizontal scroll-follow for one row,
        same idea as a single-line input, not a wrap."""
        end0 = self._fit_end(draw, font, line_text, 0, row_w)
        if cursor_col <= end0:
            return 0, end0
        start = self._fit_start(draw, font, line_text, cursor_col, row_w)
        end = self._fit_end(draw, font, line_text, start, row_w)
        return start, end

    def _draw_textarea(
        self,
        draw: 'ImageDraw.ImageDraw',
        font: 'ImageFont.ImageFont',
        layout: '_TextAreaLayout',
        value: str,
        cursor: int,
        editing: bool,
    ) -> None:
        x0, y0 = layout.x, layout.y
        x1, y1 = layout.x + layout.w, layout.y + layout.h

        cap_flash = editing and self._cap_flash_t > 0.0
        border_col = (90, 200, 255, 255) if editing else (90, 146, 186, 255)
        if cap_flash:
            border_col = (235, 110, 90, 255)
        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=7,
            fill=(7, 10, 20, 232),
            outline=border_col,
            width=2,
        )
        if editing:
            # Inner glow when active
            draw.rounded_rectangle(
                (x0 + 1, y0 + 1, x1 - 1, y1 - 1),
                radius=6,
                outline=(0, 180, 255, 40),
                width=1,
            )

        inner_x   = x0 + layout.padding
        inner_y   = y0 + layout.padding
        row_w     = max(20.0, float(x1 - layout.padding - inner_x))
        line_h    = self._line_height(draw, font)
        vis_lines = max(1, layout.lines)

        # Logical lines (real '\n' splits -- this *is* the editable unit
        # now, one row on screen per line; no separate pixel soft-wrap).
        lines = value.split('\n')
        line_offsets: list[tuple[int, int]] = []
        pos = 0
        for ln in lines:
            line_offsets.append((pos, pos + len(ln)))
            pos += len(ln) + 1

        cursor_line_idx = len(lines) - 1
        for idx, (s, e) in enumerate(line_offsets):
            if s <= cursor <= e:
                cursor_line_idx = idx
                break

        total_lines = len(lines)
        if editing:
            # Cursor's row always becomes the top of the visible window,
            # except once scrolled all the way to the last full page --
            # this alone always keeps the cursor row in view, since
            # max_scroll + vis_lines == total_lines.
            scroll_start = max(0, min(cursor_line_idx, total_lines - vis_lines))
        else:
            scroll_start = max(0, total_lines - vis_lines)

        sel = self._tf_sel_range() if editing else None
        cursor_blink = editing and (int(self._color_phase * 2.0) % 2 == 0)

        for row in range(vis_lines):
            li = scroll_start + row
            if li >= total_lines:
                break
            line_text = lines[li]
            ls, le = line_offsets[li]
            yy = inner_y + row * line_h
            is_cursor_row = editing and li == cursor_line_idx
            cursor_col = (cursor - ls) if is_cursor_row else 0

            if is_cursor_row:
                w_start, w_end = self._row_window(draw, font, line_text, row_w, cursor_col)
            else:
                w_start, w_end = 0, self._fit_end(draw, font, line_text, 0, row_w)
            visible = line_text[w_start:w_end]

            if sel is not None:
                sel_lo, sel_hi = sel
                ov_lo = max(sel_lo, ls) - ls
                ov_hi = min(sel_hi, le) - ls
                vis_lo = max(ov_lo, w_start)
                vis_hi = min(ov_hi, w_end)
                if vis_lo < vis_hi:
                    hx0 = inner_x + draw.textlength(line_text[w_start:vis_lo], font=font)
                    hx1 = inner_x + draw.textlength(line_text[w_start:vis_hi], font=font)
                    draw.rectangle((hx0, yy, hx1, yy + line_h), fill=(50, 130, 210, 130))

            draw.text((inner_x, yy), visible, font=font, fill=(232, 238, 246, 255))

            if is_cursor_row and cursor_blink:
                cursor_px = draw.textlength(line_text[w_start:cursor_col], font=font)
                cx = min(inner_x + cursor_px, x1 - layout.padding - 2)
                draw.line([(cx, yy + 1), (cx, yy + line_h - 1)], fill=(0, 220, 255, 255), width=2)

        # Position indicator (e.g. "line 42/100 · 210/512")
        if editing:
            ls, le = line_offsets[cursor_line_idx]
            line_len = le - ls
            pos_text = f'line {line_len}/{self._max_line_chars} · {cursor} / {len(value)}'
            try:
                pb = draw.textbbox((0, 0), pos_text, font=font)
                draw.text(
                    (x1 - layout.padding - (pb[2] - pb[0]), y1 - layout.padding - (pb[3] - pb[1])),
                    pos_text,
                    font=font,
                    fill=(80, 140, 200, 180),
                )
            except Exception:
                pass

    # ── Render overlay ─────────────────────────────────────────────────────────

    def render_overlay(self, width: int, height: int, dt: float, audio: Any) -> None:  # noqa: ARG002
        if not self._enabled and not self._show_config:
            return

        if width <= 0 or height <= 0:
            return

        canvas_w = int(width)
        canvas_h = int(height)
        viewport = (0, 0, canvas_w, canvas_h)

        primary_view = getattr(self._app, '_primary_display_viewport', None)
        if callable(primary_view):
            resolved = primary_view()
            if (
                isinstance(resolved, tuple)
                and len(resolved) == 4
                and int(resolved[2]) > 0
                and int(resolved[3]) > 0
            ):
                viewport = (int(resolved[0]), int(resolved[1]), int(resolved[2]), int(resolved[3]))
                canvas_w = int(resolved[2])
                canvas_h = int(resolved[3])

        self._ctx.screen.use()
        self._ctx.viewport = viewport

        if self._enabled:
            size = (canvas_w, self._banner_height)
            # None (never a cache hit) while drip is on -- the wave/sag
            # animation changes every frame by design, so there is nothing
            # to reuse. Otherwise, quantized fade/color so a held or
            # slowly-fading line collapses many frames onto one cache key.
            cache_key = None
            if not self._drip_enabled:
                line, fade = self._current_line_fade()
                cache_key = (
                    size, line, self._quantized_fade(fade),
                    self._quantized_color(self._current_text_color()),
                    round(self._alpha, 3),
                )
            stale = (
                self._banner_tex is None
                or self._banner_tex.size != size
                or cache_key is None
                or cache_key != self._banner_cache_key
            )
            if stale:
                banner = self._build_banner_image(canvas_w)
                arr = np.asarray(banner, dtype=np.uint8)
                self._banner_tex = self._ensure_texture(self._banner_tex, size)
                self._banner_tex.write(arr.tobytes())
                self._banner_cache_key = cache_key
            y = int(canvas_h - self._banner_height)
            self._draw_texture(self._banner_tex, 0.0, float(y), float(canvas_w), float(self._banner_height), canvas_w, canvas_h)

        if self._show_config:
            modal, mw, mh = self._build_modal_image(canvas_w)
            arr2 = np.asarray(modal, dtype=np.uint8)
            self._modal_tex = self._ensure_texture(self._modal_tex, (arr2.shape[1], arr2.shape[0]))
            self._modal_tex.write(arr2.tobytes())
            x = float((canvas_w - mw) // 2)
            y = float(canvas_h - self._banner_height - mh - 10)
            self._draw_texture(self._modal_tex, x, y, float(mw), float(mh), canvas_w, canvas_h)

    def shutdown(self) -> None:
        # Drop the Control Room panel first (operator_panels.py "Lifecycle").
        unregister = getattr(self._vj_api, 'unregister_operator_panel', None)
        if callable(unregister):
            try:
                unregister('banner')
            except Exception:
                pass
        self._exit_edit_mode()
        for tex in (self._banner_tex, self._modal_tex, self._white_tex):
            if tex is not None:
                tex.release()
        if self._quad_vao is not None:
            self._quad_vao.release()
        if self._quad_vbo is not None:
            self._quad_vbo.release()
        if self._program is not None:
            self._program.release()
