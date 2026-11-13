"""Django / Django REST Framework IR normalizer.

Mapping informed by ``pyopenapi/analyzers/django_static.py`` and
``pyopenapi/analyzers/django_components_static.py``; no legacy
``genapidocs_v2`` code is copied here.
"""

from __future__ import annotations

from .normalizer import BaseNormalizer


class DjangoNormalizer(BaseNormalizer):
    framework = "django"
