from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


@pytest.fixture
def django_project(tmp_path: Path) -> Path:
    root = tmp_path / "djproj"
    write(
        root,
        "manage.py",
        """
        #!/usr/bin/env python
        import os
        import sys

        if __name__ == "__main__":
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
            from django.core.management import execute_from_command_line

            execute_from_command_line(sys.argv)
        """,
    )
    write(
        root,
        "project/settings.py",
        """
        SECRET_KEY = "x"
        DEBUG = True
        INSTALLED_APPS = ["django.contrib.contenttypes", "rest_framework", "app"]
        ROOT_URLCONF = "project.urls"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        """,
    )
    write(
        root,
        "project/urls.py",
        """
        from django.urls import include, path
        from rest_framework.routers import DefaultRouter

        from app import views

        router = DefaultRouter()
        router.register(r"documents", views.DocumentViewSet, basename="document")

        urlpatterns = [
            path("api/", include(router.urls)),
            path("health/", views.health),
        ]
        """,
    )
    write(
        root,
        "app/models.py",
        """
        from django.db import models

        class Document(models.Model):
            title = models.CharField(max_length=255)
            body = models.TextField()
            created_at = models.DateTimeField(auto_now_add=True)
        """,
    )
    write(
        root,
        "app/serializers.py",
        """
        from rest_framework import serializers

        from .models import Document

        class DocumentSerializer(serializers.ModelSerializer):
            class Meta:
                model = Document
                fields = ["id", "title", "body", "created_at"]
        """,
    )
    write(
        root,
        "app/views.py",
        """
        from rest_framework import decorators, response, viewsets
        from rest_framework.decorators import api_view

        from .models import Document
        from .serializers import DocumentSerializer

        class DocumentViewSet(viewsets.ModelViewSet):
            queryset = Document.objects.all()
            serializer_class = DocumentSerializer

            @decorators.action(detail=True, methods=["post"], url_path="publish")
            def publish(self, request, pk=None):
                return response.Response({"status": "published"})

        @api_view(["GET"])
        def health(request):
            return response.Response({"status": "ok"})
        """,
    )
    write(root, "project/__init__.py", "")
    write(root, "app/__init__.py", "")
    return root


@pytest.fixture
def flask_project(tmp_path: Path) -> Path:
    root = tmp_path / "flaskapp"
    write(
        root,
        "app.py",
        """
        from flask import Flask, jsonify, request
        from flask.views import MethodView
        from pydantic import BaseModel

        app = Flask(__name__)

        class User(BaseModel):
            id: int
            name: str
            email: str | None = None

        class UserView(MethodView):
            def get(self, user_id: int):
                return jsonify({"id": user_id})

            def put(self, user_id: int):
                return jsonify(request.json)

        app.add_url_rule("/users/<int:user_id>", view_func=UserView.as_view("user"))

        @app.get("/users")
        def list_users():
            return jsonify([])

        @app.post("/users")
        def create_user():
            return jsonify({})
        """,
    )
    return root


@pytest.fixture
def fastapi_project(tmp_path: Path) -> Path:
    root = tmp_path / "fastapiapp"
    write(
        root,
        "main.py",
        """
        from fastapi import APIRouter, FastAPI, Query
        from pydantic import BaseModel

        app = FastAPI(title="Demo")
        router = APIRouter(prefix="/items", tags=["items"])

        class Item(BaseModel):
            id: int
            name: str
            price: float

        class ItemCreate(BaseModel):
            name: str
            price: float

        @router.get("/{item_id}", response_model=Item)
        def get_item(item_id: int, q: str | None = Query(default=None)):
            ...

        @router.post("", response_model=Item)
        def create_item(payload: ItemCreate):
            ...

        app.include_router(router)
        """,
    )
    return root


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYOPENAPI_CACHE_DIR", str(tmp_path / "cache"))
    from pyopenapi.config import get_settings
    from pyopenapi.server import deps

    get_settings.cache_clear()
    deps.manager.cache_clear()
    yield
    get_settings.cache_clear()
    deps.manager.cache_clear()
