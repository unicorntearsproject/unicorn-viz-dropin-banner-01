"""Config-editor rows (0.14.0): drip + beat color on the Performance tab."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip('PIL')
_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location('banner_under_test_ce', _ROOT / 'banner_controller.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load()


def test_rows_round_trip_and_persist() -> None:
    c = object.__new__(_mod.BannerController)
    c._drip_enabled = False
    c._beat_color_enabled = True
    persisted: list[int] = []
    c._persist_runtime_state = lambda: persisted.append(1)
    rows = {r['name']: r for r in c.config_editor_settings()}
    assert rows['drip_enabled']['value'] == 0.0 and rows['beat_color_enabled']['value'] == 1.0
    assert all(r['kind'] == 'toggle' and r['hint'] for r in rows.values())
    c.set_config_setting('drip_enabled', 1.0)
    c.set_config_setting('beat_color_enabled', 0.0)
    assert (c._drip_enabled, c._beat_color_enabled) == (True, False)
    assert persisted == [1, 1]
    c.set_config_setting('unknown', 1.0)
    assert persisted == [1, 1]
