"""Flask IR normalizer.

Mapping informed by ``pyopenapi/analyzers/flask_static.py``.
"""

from __future__ import annotations

from .normalizer import BaseNormalizer


class FlaskNormalizer(BaseNormalizer):
    framework = "flask"
