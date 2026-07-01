"""Behavior tests for the hardened admin-token dependency.

Covers the three security properties added in the auth hardening pass:
Bearer-header-only (no ?token= query param), constant-time comparison,
and fail-closed when ADMIN_TOKEN is missing in production.
"""
import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from voteiq.api.dependencies import require_admin_token

TOKEN_VARS = (
    "ADMIN_TOKEN",
    "VOTEIQ_ADMIN_TOKEN",
    "UPLOAD_ADMIN_TOKEN",
    "POLL_INGEST_ADMIN_TOKEN",
    "FLASK_ENV",
    "RENDER",
)


@pytest.fixture()
def client(monkeypatch):
    for var in TOKEN_VARS:
        monkeypatch.delenv(var, raising=False)
    app = FastAPI()

    @app.get("/guarded")
    def guarded(_: None = Depends(require_admin_token)):
        return {"ok": True}

    return TestClient(app)


def test_dev_mode_no_token_passes(client):
    assert client.get("/guarded").status_code == 200


def test_production_without_token_fails_closed(client, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    assert client.get("/guarded").status_code == 503


def test_render_env_without_token_fails_closed(client, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    assert client.get("/guarded").status_code == 503


def test_missing_header_rejected(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert client.get("/guarded").status_code == 403


def test_wrong_bearer_rejected(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    resp = client.get("/guarded", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 403


def test_correct_bearer_accepted(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    resp = client.get("/guarded", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200


def test_legacy_query_param_no_longer_authenticates(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert client.get("/guarded?token=s3cret").status_code == 403
