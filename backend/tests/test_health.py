"""GET /api/v1/health (T-10, FR-OPS-01): công khai, nhanh, không lộ địa chỉ/token."""
from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.health import HealthChecker
from app.core.ollama import probe_ollama, validate_ollama_url

SECRET_HOST = "abc-secret-tunnel.trycloudflare.com"
URL = f"https://{SECRET_HOST}"
TOKEN = "tok-very-secret-123"


def colab_settings(settings: Settings, **kw) -> Settings:
    data = settings.model_dump() | {"ollama_url": URL, "ollama_api_key": TOKEN, "_env_file": None} | kw
    return Settings(**data)


def mock(handler):
    return httpx.MockTransport(handler)


def tags(*names):
    return lambda req: httpx.Response(200, json={"models": [{"name": n} for n in names]})


# ---- validate_ollama_url ----

@pytest.mark.parametrize("url,ok", [
    (URL, True),
    ("https://a.b.ngrok-free.app/", True),
    ("http://x.trycloudflare.com", False),
    ("https://evil.com", False),
    ("https://trycloudflare.com.evil.com", False),
    ("https://eviltrycloudflare.com", False),
    ("https://user:pw@x.trycloudflare.com", False),
    ("https://x.trycloudflare.com:99999", False),
    ("ftp://x.trycloudflare.com", False),
    ("not a url", False),
])
def test_validate_url(url, ok):
    assert (validate_ollama_url(url, ["trycloudflare.com", "ngrok-free.app"]) is None) is ok


# ---- probe_ollama ----

def test_probe_not_configured(settings):
    assert probe_ollama(settings) == {"status": "not_configured", "reason": "not_configured"}


def test_probe_invalid_url_never_calls_network(settings):
    s = colab_settings(settings, ollama_url="http://x.trycloudflare.com")
    def boom(req):
        raise AssertionError("không được gọi mạng")
    assert probe_ollama(s, transport=mock(boom)) == {"status": "down", "reason": "invalid_url"}


def test_probe_up_sends_bearer_and_no_redirect(settings):
    seen = {}
    def h(req):
        seen["auth"] = req.headers.get("authorization")
        seen["path"] = req.url.path
        return httpx.Response(200, json={"models": []})
    assert probe_ollama(colab_settings(settings), transport=mock(h)) == {"status": "up"}
    assert seen == {"auth": f"Bearer {TOKEN}", "path": "/api/tags"}


def test_probe_redirect_is_not_followed(settings):
    def h(req):
        return httpx.Response(302, headers={"location": "https://evil.com/api/tags"})
    assert probe_ollama(colab_settings(settings), transport=mock(h)) == {"status": "down", "reason": "bad_response"}


@pytest.mark.parametrize("resp,reason", [
    (httpx.Response(401), "unauthorized"),
    (httpx.Response(403), "unauthorized"),
    (httpx.Response(500), "bad_response"),
    (httpx.Response(200, text="<html>"), "unreachable"),
    (httpx.Response(200, json=[1]), "bad_response"),
    (httpx.Response(200, json={"models": "x"}), "bad_response"),
])
def test_probe_bad_responses(settings, resp, reason):
    assert probe_ollama(colab_settings(settings), transport=mock(lambda r: resp)) == {"status": "down", "reason": reason}


def test_probe_network_error_does_not_leak(settings):
    def h(req):
        raise httpx.ConnectError(f"cannot connect to {URL} with {TOKEN}")
    out = probe_ollama(colab_settings(settings), transport=mock(h))
    assert out == {"status": "down", "reason": "unreachable"}
    assert SECRET_HOST not in json.dumps(out) and TOKEN not in json.dumps(out)


def test_probe_model_check(settings):
    s = colab_settings(settings, ollama_model="llama3")
    assert probe_ollama(s, transport=mock(tags("llama3:latest")))["status"] == "up"
    assert probe_ollama(s, transport=mock(tags("llama3")))["status"] == "up"
    assert probe_ollama(s, transport=mock(tags("other:7b"))) == {"status": "down", "reason": "model_missing"}


# ---- HealthChecker ----

class FakeOrch:
    def __init__(self, alive=True):
        self.alive = alive
    def health(self):
        return {"worker_alive": self.alive, "queue_size": 2, "busy": True}


def make_checker(settings, probe, alive=True, which=lambda n: "/usr/bin/ffmpeg", clock=None, factory=None):
    kw = {"clock": clock} if clock else {}
    return HealthChecker(settings, factory or (lambda: _Sess()), FakeOrch(alive), "9.9.9", probe=probe, which=which, **kw)


class _Sess:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *_): return None


UP = lambda s: {"status": "up"}


def test_checker_ok(settings):
    code, body = make_checker(settings, UP).check()
    assert code == 200 and body["status"] == "ok" and body["version"] == "9.9.9"
    assert body["components"] == {
        "database": {"status": "up"},
        "worker": {"status": "up", "queue_size": 2, "busy": True},
        "ffmpeg": {"status": "up"},
        "ollama": {"status": "up"},
    }


def test_ollama_down_is_degraded_but_200(settings):
    code, body = make_checker(settings, lambda s: {"status": "down", "reason": "unreachable"}).check()
    assert code == 200 and body["status"] == "degraded"
    assert body["components"]["ollama"] == {"status": "down", "reason": "unreachable"}


def test_ffmpeg_missing_is_degraded(settings):
    code, body = make_checker(settings, UP, which=lambda n: None).check()
    assert code == 200 and body["status"] == "degraded" and body["components"]["ffmpeg"] == {"status": "down"}


def test_worker_down_is_503(settings):
    code, body = make_checker(settings, UP, alive=False).check()
    assert code == 503 and body["status"] == "down" and body["components"]["worker"]["status"] == "down"


def test_db_down_is_503(settings):
    class Bad:
        def __enter__(self): raise RuntimeError("db at /secret/path")
        def __exit__(self, *a): return False
    code, body = make_checker(settings, UP, factory=lambda: Bad()).check()
    assert code == 503 and body["components"]["database"] == {"status": "down"}
    assert "/secret/path" not in json.dumps(body)


def test_probe_exception_becomes_down(settings):
    def boom(s): raise RuntimeError(f"{URL} {TOKEN}")
    code, body = make_checker(settings, boom).check()
    assert code == 200 and body["components"]["ollama"] == {"status": "down", "reason": "unreachable"}
    assert SECRET_HOST not in json.dumps(body)


def test_ollama_result_cached_30s(settings):
    calls, now = [], [100.0]
    def probe(s):
        calls.append(1)
        return {"status": "up"}
    c = make_checker(settings, probe, clock=lambda: now[0])
    c.check(); c.check()
    now[0] += 29
    c.check()
    assert len(calls) == 1
    now[0] += 2
    c.check()
    assert len(calls) == 2


def test_probe_extra_keys_dropped(settings):
    c = make_checker(settings, lambda s: {"status": "up", "url": URL, "token": TOKEN})
    _, body = c.check()
    assert body["components"]["ollama"] == {"status": "up"}


# ---- endpoint ----

def test_endpoint_is_public_and_shaped(client):
    r = client.get("/api/v1/health")  # không token
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"status", "version", "checked_at", "components"}
    assert set(body["components"]) == {"database", "worker", "ffmpeg", "ollama"}
    assert body["components"]["database"]["status"] == "up"
    assert body["components"]["worker"]["status"] == "up"
    assert body["components"]["ollama"] == {"status": "not_configured", "reason": "not_configured"}


def test_endpoint_no_leak_when_colab_configured(settings, app, caplog, monkeypatch):
    from app.main import create_app
    from tests.conftest import alembic_upgrade
    s = colab_settings(settings, ollama_api_key=TOKEN)
    application = create_app(s)
    alembic_upgrade(application.state.engine)
    seen = {}
    real = httpx.Client

    def patched(*a, **kw):
        kw["transport"] = mock(lambda req: (_ for _ in ()).throw(httpx.ConnectError(f"{URL} {TOKEN}")))
        return real(*a, **kw)

    monkeypatch.setattr("app.core.ollama.httpx.Client", patched)
    caplog.set_level(logging.DEBUG)
    with TestClient(application) as c:
        r = c.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["components"]["ollama"] == {"status": "down", "reason": "unreachable"}
    blob = r.text + caplog.text
    assert SECRET_HOST not in blob and TOKEN not in blob
    application.state.engine.dispose()


def test_endpoint_503_when_worker_stopped(app):
    with TestClient(app) as c:
        app.state.orchestrator.stop()
        r = c.get("/api/v1/health")
    assert r.status_code == 503 and r.json()["status"] == "down"
