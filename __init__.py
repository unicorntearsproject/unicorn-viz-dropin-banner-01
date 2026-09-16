"""banner-01 drop-in package."""
from __future__ import annotations

try:
    from .banner_controller import BannerController
except ImportError:  # imported bare, e.g. pytest collecting the drop-in's own tests
    BannerController = None  # type: ignore[assignment]

__all__ = ['BannerController']
