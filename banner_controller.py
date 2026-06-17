"""Bottom-edge marquee banner overlay drop-in.

Provides a stream-style scrolling text banner that can be toggled and configured
at runtime without modifying core overlay internals.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import moderngl
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from unicornviz.paths import resolve_path

log = logging.getLogger(__name__)

DROPIN_CAPABILITIES = {
    'name': 'banner',
    'class_symbol': 'BannerController',
    'subsystem_name': 'banner',
    'key_handler_attr': 'handle_key',
}

HELP_ENTRIES = [
    ('Banner', 'B', 'Toggle bottom marquee banner on/off'),
    ('Banner', 'Ctrl+B', 'Toggle banner config modal'),
    ('Banner', 'Ctrl+R', 'Reset banner text to default (in config modal)'),
    ('Banner', 'Arrows', 'Adjust speed/font while config modal is open'),
    ('Banner', 'Enter', 'Toggle live text edit mode in config modal'),
]

_DEFAULT_TEXT = (
    'You are watching the Unicorn Tears Stream brought to you by DJ Tears, '
    'visualization by Unicorn-Viz.  Follow DJ Unicorn Tears on all your '
    'favorite platforms!  Visit UnicornTears.com to download Unicorn-Viz for '
    'free!  Please help us grow - like, share & Follow!  Monthly subscribers '
    'get early access to music, videos & software!'
)

_FONT_CANDIDATES = [
    resolve_path('assets/fonts/ui-font.ttf'),
    Path('/usr/share/fonts/adobe-source-code-pro-fonts/SourceCodePro-Medium.otf'),
    Path('/usr/share/fonts/google-noto-vf/NotoSansMono[wght].ttf'),
    Path('/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf'),
]


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
    """Render a configurable bottom-edge marquee strip over all effects."""

    HELP_ENTRIES = HELP_ENTRIES

    def __init__(self, app: Any, cfg: dict[str, Any] | None = None) -> None:
        self._app = app
        self._cfg = dict(cfg or {})
        self._ctx: moderngl.Context = app.vj_api.ctx
        self._vj_api = app.vj_api

        self._enabled = bool(self._cfg.get('enabled', True))
        self._show_config = False
        self._edit_mode = False

        self._text = str(self._cfg.get('text', _DEFAULT_TEXT) or _DEFAULT_TEXT).strip()
        if not self._text:
            self._text = _DEFAULT_TEXT

        self._scroll_speed = float(self._cfg.get('scroll_speed', 140.0) or 140.0)
        self._font_px = int(self._cfg.get('font_px', 28) or 28)
        self._beat_color_enabled = bool(self._cfg.get('beat_color_enabled', True))
        self._alpha = float(self._cfg.get('alpha', 0.88) or 0.88)
        self._state_path = self._resolve_state_path(self._cfg.get('state_file', 'logs/banner-state.json'))

        self._scroll_speed = max(40.0, min(420.0, self._scroll_speed))
        self._font_px = max(16, min(56, self._font_px))
        self._alpha = max(0.35, min(1.0, self._alpha))

        self._pad_x = 14
        self._pad_y = 6
        self._gap_px = 180
        self._offset_px = 0.0
        self._color_phase = 0.0

        self._bass = 0.0
        self._mid = 0.0
        self._treble = 0.0
        self._beat = 0.0

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

        self._restore_local_state()
        self._restore_runtime_state()

    def _resolve_state_path(self, raw: Any) -> Path:
        candidate = str(raw or 'logs/banner-state.json').strip()
        if not candidate:
            candidate = 'logs/banner-state.json'
        return resolve_path(candidate)

    def _read_local_state(self) -> dict[str, Any]:
        try:
            if self._state_path.exists():
                payload = json.loads(self._state_path.read_text(encoding='utf-8'))
                if isinstance(payload, dict):
                    return payload
        except Exception as exc:
            log.warning('Banner state read failed from %s: %s', self._state_path, exc)
        return {}

    def _write_local_state(self, payload: dict[str, Any]) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding='utf-8',
            )
        except Exception as exc:
            log.warning('Banner state write failed to %s: %s', self._state_path, exc)

    def _apply_state_payload(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        text = str(raw.get('text', self._text) or self._text).strip()
        if text:
            self._text = text
        self._enabled = bool(raw.get('enabled', self._enabled))
        self._scroll_speed = max(
            40.0,
            min(
                420.0,
                float(raw.get('scroll_speed', self._scroll_speed) or self._scroll_speed),
            ),
        )
        self._font_px = max(
            16,
            min(56, int(raw.get('font_px', self._font_px) or self._font_px)),
        )
        self._beat_color_enabled = bool(
            raw.get('beat_color_enabled', self._beat_color_enabled),
        )
        self._alpha = max(
            0.35,
            min(1.0, float(raw.get('alpha', self._alpha) or self._alpha)),
        )
        self._font = self._load_font(self._font_px)
        self._banner_height = self._compute_banner_height()

    def _restore_local_state(self) -> None:
        self._apply_state_payload(self._read_local_state())

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for path in _FONT_CANDIDATES:
            try:
                if path.exists():
                    return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue
        return ImageFont.load_default()

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
            'scroll_speed': float(self._scroll_speed),
            'font_px': int(self._font_px),
            'beat_color_enabled': bool(self._beat_color_enabled),
            'alpha': float(self._alpha),
        }
        self._vj_api.set_runtime_state('banner', payload)
        self._write_local_state(payload)

    def _restore_runtime_state(self) -> None:
        self._apply_state_payload(self._vj_api.get_runtime_state('banner', default={}))

    def _set_font_px(self, value: int) -> None:
        value = max(16, min(56, int(value)))
        if value == self._font_px:
            return
        self._font_px = value
        self._font = self._load_font(self._font_px)
        self._banner_height = self._compute_banner_height()

    def _sym_to_char(self, sym: int, mod: int) -> str:
        import sdl2  # noqa: PLC0415

        shift = bool(mod & sdl2.KMOD_SHIFT)

        if sdl2.SDLK_a <= sym <= sdl2.SDLK_z:
            c = chr(ord('a') + (sym - sdl2.SDLK_a))
            return c.upper() if shift else c
        if sdl2.SDLK_0 <= sym <= sdl2.SDLK_9:
            idx = sym - sdl2.SDLK_0
            if shift:
                shifted = ')!@#$%^&*('
                return shifted[idx]
            return str(idx)

        punctuation = {
            sdl2.SDLK_SPACE: ' ',
            sdl2.SDLK_PERIOD: '>' if shift else '.',
            sdl2.SDLK_COMMA: '<' if shift else ',',
            sdl2.SDLK_SEMICOLON: ':' if shift else ';',
            sdl2.SDLK_QUOTE: '"' if shift else "'",
            sdl2.SDLK_SLASH: '?' if shift else '/',
            sdl2.SDLK_MINUS: '_' if shift else '-',
            sdl2.SDLK_EQUALS: '+' if shift else '=',
            sdl2.SDLK_LEFTBRACKET: '{' if shift else '[',
            sdl2.SDLK_RIGHTBRACKET: '}' if shift else ']',
            sdl2.SDLK_BACKSLASH: '|' if shift else '\\',
            sdl2.SDLK_AMPERSAND: '&',
            sdl2.SDLK_EXCLAIM: '!',
            sdl2.SDLK_COLON: ':',
        }
        return punctuation.get(sym, '')

    def handle_key(self, sym: int, mod: int) -> 'str | None | bool':
        import sdl2  # noqa: PLC0415

        ctrl = bool(mod & sdl2.KMOD_CTRL)
        alt = bool(mod & sdl2.KMOD_ALT)
        gui = bool(mod & sdl2.KMOD_GUI)
        shift = bool(mod & sdl2.KMOD_SHIFT)

        # Special-case U while config is open so replay-splash never steals it.
        if self._show_config and sym == sdl2.SDLK_u and not (ctrl or alt or gui):
            if self._edit_mode:
                self._text += 'U' if shift else 'u'
                self._persist_runtime_state()
            return True

        # While editing text, consume plain key presses first so runtime/global
        # shortcuts do not fire from typing input.
        if self._show_config and self._edit_mode:
            if sym == sdl2.SDLK_ESCAPE:
                self._edit_mode = False
                self._show_config = False
                self._persist_runtime_state()
                return 'Banner config: OFF'

            if sym in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                if bool(mod & sdl2.KMOD_SHIFT):
                    self._text += '\n'
                    self._persist_runtime_state()
                    return 'Banner text: new line'
                self._edit_mode = False
                self._persist_runtime_state()
                return 'Banner text edit: OFF'

            if sym == sdl2.SDLK_BACKSPACE:
                if self._text:
                    self._text = self._text[:-1]
                    self._persist_runtime_state()
                return 'Banner text: delete'

            if sym == sdl2.SDLK_TAB:
                self._text += '    '
                self._persist_runtime_state()
                return 'Banner text: tab'

            # Ignore control/meta combos while text editing.
            if ctrl or alt or gui:
                return True

            ch = self._sym_to_char(sym, mod)
            if ch:
                self._text += ch
                self._persist_runtime_state()
                return True
            return True

        if sym == sdl2.SDLK_b and ctrl:
            self._show_config = not self._show_config
            if not self._show_config:
                self._edit_mode = False
                self._persist_runtime_state()
            return f'Banner config: {"ON" if self._show_config else "OFF"}'

        if sym == sdl2.SDLK_b and not ctrl and not (mod & (sdl2.KMOD_ALT | sdl2.KMOD_SHIFT)):
            self._enabled = not self._enabled
            self._persist_runtime_state()
            return f'Banner: {"ON" if self._enabled else "OFF"}'

        if not self._show_config:
            return False

        if sym == sdl2.SDLK_ESCAPE:
            self._edit_mode = False
            self._show_config = False
            self._persist_runtime_state()
            return 'Banner config: OFF'

        if sym in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
            self._edit_mode = not self._edit_mode
            if not self._edit_mode:
                self._persist_runtime_state()
            return 'Banner text edit: ON' if self._edit_mode else 'Banner text edit: OFF'

        if sym == sdl2.SDLK_LEFT:
            self._scroll_speed = max(40.0, self._scroll_speed - 10.0)
            self._persist_runtime_state()
            return f'Banner speed: {self._scroll_speed:.0f}px/s'
        if sym == sdl2.SDLK_RIGHT:
            self._scroll_speed = min(420.0, self._scroll_speed + 10.0)
            self._persist_runtime_state()
            return f'Banner speed: {self._scroll_speed:.0f}px/s'
        if sym == sdl2.SDLK_UP:
            self._set_font_px(self._font_px + 1)
            self._persist_runtime_state()
            return f'Banner font: {self._font_px}px'
        if sym == sdl2.SDLK_DOWN:
            self._set_font_px(self._font_px - 1)
            self._persist_runtime_state()
            return f'Banner font: {self._font_px}px'
        if sym == sdl2.SDLK_c and ctrl:
            self._beat_color_enabled = not self._beat_color_enabled
            self._persist_runtime_state()
            return f'Banner beat color: {"ON" if self._beat_color_enabled else "OFF"}'
        if sym == sdl2.SDLK_r and ctrl:
            self._text = _DEFAULT_TEXT
            self._persist_runtime_state()
            return 'Banner text reset'

        return True

    def update(self, dt: float, audio: Any) -> None:
        self._bass = float(getattr(audio, 'bass_n', 0.0) or 0.0)
        self._mid = float(getattr(audio, 'mid_n', 0.0) or 0.0)
        self._treble = float(getattr(audio, 'treble_n', 0.0) or 0.0)
        self._beat = float(getattr(audio, 'beat', 0.0) or 0.0)

        if self._enabled:
            self._offset_px += self._scroll_speed * max(0.0, float(dt))
        self._color_phase += max(0.0, float(dt)) * (0.55 + self._mid * 1.4)

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
        x: float,
        y: float,
        w: float,
        h: float,
        canvas_w: int,
        canvas_h: int,
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

    def _build_banner_image(self, width: int) -> Image.Image:
        h = self._banner_height
        img = Image.new('RGBA', (width, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        bg_alpha = int(255 * self._alpha)
        draw.rectangle((0, 0, width, h), fill=(8, 10, 18, bg_alpha))
        draw.rectangle((0, 0, width, 2), fill=(70, 170, 255, min(255, bg_alpha + 20)))

        measure = draw.textlength(self._text, font=self._font)
        loop_w = max(width + 40, int(measure) + self._gap_px)
        x0 = int(width - (self._offset_px % loop_w))

        color = self._current_text_color()
        y = self._pad_y - 1
        draw.text((x0, y), self._text, font=self._font, fill=color)
        draw.text((x0 + loop_w, y), self._text, font=self._font, fill=color)
        return img

    def _build_modal_image(self, width: int) -> tuple[Image.Image, int, int]:
        panel_w = min(width - 40, 1260)
        panel_h = 284
        panel = Image.new('RGBA', (panel_w, panel_h), (9, 12, 24, 240))
        draw = ImageDraw.Draw(panel)

        draw.rectangle((0, 0, panel_w - 1, panel_h - 1), outline=(72, 186, 255, 255), width=2)
        title_font = self._load_font(max(18, self._font_px - 4))
        body_font = self._load_font(max(14, self._font_px - 9))
        mono_font = self._load_font(max(14, self._font_px - 10))

        draw.text((14, 10), 'BANNER CONFIG', font=title_font, fill=(170, 220, 255, 255))
        status = 'ON' if self._enabled else 'OFF'
        draw.text((250, 12), f'Status: {status}', font=body_font, fill=(230, 236, 246, 255))
        draw.text((380, 12), f'Speed: {self._scroll_speed:.0f}px/s', font=body_font, fill=(230, 236, 246, 255))
        draw.text((560, 12), f'Font: {self._font_px}px', font=body_font, fill=(230, 236, 246, 255))
        draw.text((700, 12), f'Beat Color: {"ON" if self._beat_color_enabled else "OFF"}', font=body_font, fill=(230, 236, 246, 255))
        self._draw_config_sprite(draw, panel_w)

        draw.text((14, 44), 'B: toggle banner   Ctrl+B: close config   Enter: text edit mode', font=body_font, fill=(184, 204, 224, 255))
        draw.text((14, 64), 'Left/Right: speed   Up/Down: font   Ctrl+C: beat color   Ctrl+R: reset text', font=body_font, fill=(184, 204, 224, 255))
        draw.text((14, 84), 'Shift+Enter: new line   Backspace: delete   Tab: spaces', font=body_font, fill=(184, 204, 224, 255))

        edit_state = 'EDITING' if self._edit_mode else 'VIEW'
        edit_color = (255, 214, 110, 255) if self._edit_mode else (176, 196, 220, 255)
        draw.text((14, 106), f'Mode: {edit_state}', font=body_font, fill=edit_color)

        textarea = _TextAreaLayout(
            x=14,
            y=128,
            w=panel_w - 28,
            h=panel_h - 142,
            padding=10,
            lines=4,
        )
        self._draw_textarea(
            draw=draw,
            font=mono_font,
            layout=textarea,
            value=self._text,
            editing=self._edit_mode,
        )

        return panel, panel_w, panel_h

    def _draw_config_sprite(self, draw: ImageDraw.ImageDraw, panel_w: int) -> None:
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
            yy = y0 + 7 + int(frac * (sprite_size - 14))
            glow = 96 + int(120 * (1.0 - abs(frac - 0.5) * 2.0))
            draw.line(
                (x0 + 8, yy, x1 - 8, yy),
                fill=(30, min(255, glow), min(255, glow + 30), 220),
                width=1,
            )

        cx = x0 + sprite_size / 2.0 + math.sin(phase * 1.35) * (sprite_size * 0.22)
        cy = y0 + sprite_size / 2.0 + math.cos(phase * 1.95) * (sprite_size * 0.22)
        radius = 5 + int(2 * min(1.0, self._beat + self._treble * 0.5))
        glow = (255, 140, 120, 235)
        draw.ellipse(
            (cx - radius - 3, cy - radius - 3, cx + radius + 3, cy + radius + 3),
            fill=(60, 120, 200, 130),
        )
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=glow)

    def _line_height(self, draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> int:
        bbox = draw.textbbox((0, 0), 'Ag', font=font)
        return max(16, int(bbox[3] - bbox[1]))

    def _wrap_to_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        if max_width <= 8:
            return [text]

        def _break_long_word(word: str) -> list[str]:
            chunks: list[str] = []
            pending = ''
            for ch in word:
                candidate = pending + ch
                if draw.textlength(candidate, font=font) <= max_width:
                    pending = candidate
                    continue
                if pending:
                    chunks.append(pending)
                pending = ch
            if pending:
                chunks.append(pending)
            return chunks or ['']

        lines: list[str] = []
        for raw in text.split('\n'):
            paragraph = raw.rstrip('\r')
            if not paragraph:
                lines.append('')
                continue
            words = paragraph.split(' ')
            current = ''
            for word in words:
                chunk = word if current == '' else f'{current} {word}'
                if draw.textlength(chunk, font=font) <= max_width:
                    current = chunk
                    continue
                if current:
                    lines.append(current)
                if draw.textlength(word, font=font) <= max_width:
                    current = word
                else:
                    pieces = _break_long_word(word)
                    lines.extend(pieces[:-1])
                    current = pieces[-1]
            lines.append(current)
        return lines or ['']

    def _draw_textarea(
        self,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
        layout: _TextAreaLayout,
        value: str,
        editing: bool,
    ) -> None:
        x0, y0 = layout.x, layout.y
        x1, y1 = layout.x + layout.w, layout.y + layout.h

        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=7,
            fill=(7, 10, 20, 232),
            outline=(90, 146, 186, 255),
            width=2,
        )

        inner_x = x0 + layout.padding
        inner_y = y0 + layout.padding
        inner_w = max(20, layout.w - layout.padding * 2)
        line_height = self._line_height(draw, font)

        wrapped = self._wrap_to_width(draw, value, font, inner_w)
        visible_lines = max(1, layout.lines)

        cursor_visible = editing and (int(self._color_phase * 2.0) % 2 == 0)
        if cursor_visible:
            if wrapped:
                wrapped[-1] = wrapped[-1] + '_'
            else:
                wrapped = ['_']

        shown = wrapped[-visible_lines:]
        for idx in range(visible_lines):
            line = shown[idx] if idx < len(shown) else ''
            yy = inner_y + idx * line_height
            draw.text((inner_x, yy), line, font=font, fill=(232, 238, 246, 255))

    def render_overlay(self, width: int, height: int, dt: float, audio: Any) -> None:  # noqa: ARG002
        if not self._enabled and not self._show_config:
            return

        if width <= 0 or height <= 0:
            return

        self._ctx.screen.use()
        self._ctx.viewport = (0, 0, int(width), int(height))

        # Banner strip
        if self._enabled:
            banner = self._build_banner_image(int(width))
            arr = np.asarray(banner, dtype=np.uint8)
            self._banner_tex = self._ensure_texture(self._banner_tex, (arr.shape[1], arr.shape[0]))
            self._banner_tex.write(arr.tobytes())
            y = int(height - self._banner_height)
            self._draw_texture(
                self._banner_tex,
                0.0,
                float(y),
                float(width),
                float(self._banner_height),
                int(width),
                int(height),
            )

        # Config modal
        if self._show_config:
            modal, mw, mh = self._build_modal_image(int(width))
            arr2 = np.asarray(modal, dtype=np.uint8)
            self._modal_tex = self._ensure_texture(self._modal_tex, (arr2.shape[1], arr2.shape[0]))
            self._modal_tex.write(arr2.tobytes())
            x = float((width - mw) // 2)
            y = float(height - self._banner_height - mh - 10)
            self._draw_texture(
                self._modal_tex,
                x,
                y,
                float(mw),
                float(mh),
                int(width),
                int(height),
            )

    def shutdown(self) -> None:
        for tex in (self._banner_tex, self._modal_tex, self._white_tex):
            if tex is not None:
                tex.release()
        if self._quad_vao is not None:
            self._quad_vao.release()
        if self._quad_vbo is not None:
            self._quad_vbo.release()
        if self._program is not None:
            self._program.release()
