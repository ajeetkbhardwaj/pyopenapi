"""FastAPI IR normalizer.

Mapping informed by ``pyopenapi/analyzers/fastapi_static.py``.
"""

from __future__ import annotations

from .normalizer import BaseNormalizer


class FastAPINormalizer(BaseNormalizer):
    framework = "fastapi"
