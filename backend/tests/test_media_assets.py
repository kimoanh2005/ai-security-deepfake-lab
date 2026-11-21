"""T-13: tải tệp an toàn và /media-assets (FR-DF-01, FR-DF-02, FR-DF-08)."""
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.core import files
from app.core.errors import AppError
from app.main import create_app
from tests.conftest import alembic_upgrade

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg để sinh video thử")

FIELDS = {"label": "synthetic", "source": "unit test", "consent_ref": "CONSENT-1", "technique": "faceswap"}


def make_video(path: Path, seconds: int = 1, fmt: str = "mp4") -> bytes:
    codec = ["-c:v", "libvpx", "-b:v", "50k"] if fmt == "webm" else ["-c:v", "mpeg4"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=64x48:rate=10:duration={seconds}",
                    *codec, str(path)], check=True)
    return path.read_bytes()


@pytest.fixture(scope="module")
def mp4(tmp_path_factory) -> bytes:
    return make_video(tmp_path_factory.mktemp("v") / "a.mp4")


@pytest.fixture()
def user_h(make_user, login):
    return login(*make_user())


@pytest.fixture()
def admin_h(make_user, login):
    return login(*make_user("admin@lab.local", "admin-pass-123", role="admin"))


def upload(client, headers, data=b"", name="clip.mp4", fields=FIELDS, ctype="video/mp4"):
    return client.post("/api/v1/media-assets", headers=headers, data=fields, files={"file": (name, data, ctype)})


def stored(settings):
    return [p for p in settings.storage_path.iterdir() if p.is_file()]


def test_upload_ok(client, user_h, mp4, settings):
    r = upload(client, user_h, mp4)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["id"].startswith("med_") and r.headers["Location"] == f"/api/v1/media-assets/{b['id']}"
    assert b["origin"] == "upload" and b["label"] == "synthetic" and b["mime"] == "video/mp4"
    assert b["size_bytes"] == len(mp4) and len(b["sha256"]) == 64
    assert b["width"] == 64 and b["height"] == 48 and b["fps"] == 10 and 0.9 < b["duration_s"] < 1.2
    (f,) = stored(settings)
    assert f.suffix == ".mp4" and "clip" not in f.name and len(f.stem) == 32  # tên ngẫu nhiên, không dùng tên client
    assert not list((settings.storage_path / ".incoming").iterdir())


def test_webm_ok(client, user_h, tmp_path):
    r = upload(client, user_h, make_video(tmp_path / "a.webm", fmt="webm"), name="a.webm", ctype="video/webm")
    assert r.status_code == 201, r.text
    assert r.json()["mime"] == "video/webm"


def test_requires_auth(client, mp4):
    assert upload(client, {}, mp4).status_code == 401


def test_renamed_extension_rejected(client, user_h, settings):
    r = upload(client, user_h, b"MZ\x90\x00" + b"\x00" * 200, name="evil.mp4")
    assert r.status_code == 415 and r.json()["code"] == "UNSUPPORTED_MEDIA"
    assert stored(settings) == []


def test_text_file_with_mp4_content_type_rejected(client, user_h):
    r = upload(client, user_h, b"#!/bin/sh\necho hi\n", name="a.mp4", ctype="video/mp4")
    assert r.status_code == 415


def test_magic_ok_but_not_a_real_video(client, user_h, settings):
    fake = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
    r = upload(client, user_h, fake)
    assert r.status_code == 415 and r.json()["code"] == "UNSUPPORTED_MEDIA"
    assert stored(settings) == [] and not list((settings.storage_path / ".incoming").iterdir())


def test_empty_file(client, user_h):
    assert upload(client, user_h, b"").status_code == 422


def test_too_large_streamed(tmp_path, make_user, mp4):
    s = Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", db_path=str(tmp_path / "l.db"),
                 storage_dir=str(tmp_path / "st"), login_rate_limit_per_minute=1000, max_upload_mb=1)
    app = create_app(s)
    alembic_upgrade(app.state.engine)
    from fastapi.testclient import TestClient
    from app.core.security import hash_password
    from app.models import User
    with app.state.session_factory() as db:
        db.add(User(email="u@x.io", password_hash=hash_password("pw-pass-1234", 4), full_name="u", role="user", status="active"))
        db.commit()
    with TestClient(app) as c:
        tok = c.post("/api/v1/auth/login", json={"email": "u@x.io", "password": "pw-pass-1234"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        big = mp4 + b"\x00" * (1024 * 1024)
        r = upload(c, h, big)
        assert r.status_code == 413 and r.json()["code"] == "FILE_TOO_LARGE"
        assert [p for p in s.storage_path.iterdir() if p.is_file()] == []
    app.state.engine.dispose()


def test_too_long_video(client, user_h, mp4, settings, tmp_path):
    settings.max_video_seconds = 0.5
    r = upload(client, user_h, mp4)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    assert stored(settings) == []


def test_missing_consent(client, user_h, mp4, settings):
    for bad in (None, "", "   "):
        f = {k: v for k, v in FIELDS.items() if k != "consent_ref"}
        if bad is not None:
            f["consent_ref"] = bad
        r = upload(client, user_h, mp4, fields=f)
        assert r.status_code == 422 and r.json()["code"] == "MISSING_CONSENT"
    assert stored(settings) == []


def test_other_field_errors(client, user_h, mp4):
    r = upload(client, user_h, mp4, fields={"label": "fake", "consent_ref": "c"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    assert {e["field"] for e in r.json()["errors"]} == {"label", "source"}


def test_missing_file_part(client, user_h):
    r = client.post("/api/v1/media-assets", headers=user_h, data=FIELDS)
    assert r.status_code in (400, 422)
    assert r.headers["content-type"].startswith("application/problem+json")


def test_not_multipart(client, user_h):
    r = client.post("/api/v1/media-assets", headers=user_h, json=FIELDS)
    assert r.status_code == 400 and r.json()["code"] == "MALFORMED_REQUEST"


def test_path_traversal_filename_not_used(client, user_h, mp4, settings):
    r = upload(client, user_h, mp4, name="../../etc/passwd.mp4")
    assert r.status_code == 201
    (f,) = stored(settings)
    assert f.resolve().parent == settings.storage_path.resolve()


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", "..", ".", "", "/etc/passwd", "a\x00b"])
def test_safe_storage_path_rejects(tmp_path, name):
    with pytest.raises(AppError):
        files.safe_storage_path(tmp_path, name)


def test_safe_storage_path_blocks_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "link").symlink_to(outside)
    with pytest.raises(AppError):
        files.safe_storage_path(storage, "link")
    assert files.safe_storage_path(storage, "ok.mp4") == storage.resolve() / "ok.mp4"


def test_file_rejected_is_logged_without_filename(client, user_h, caplog):
    with caplog.at_level(logging.WARNING, logger="app.files"):
        upload(client, user_h, b"not a video at all", name="secret-name.mp4")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("file_rejected reason=bad_magic" in m for m in msgs)
    assert not any("secret-name" in m for m in msgs)


def test_get_list_filters_and_etag(client, user_h, mp4):
    a = upload(client, user_h, mp4).json()
    upload(client, user_h, mp4, fields={**FIELDS, "label": "original"})
    r = client.get("/api/v1/media-assets", headers=user_h)
    assert r.status_code == 200 and r.json()["total"] == 2
    assert client.get("/api/v1/media-assets?label=original", headers=user_h).json()["total"] == 1
    assert client.get("/api/v1/media-assets?origin=sample", headers=user_h).json()["total"] == 0
    assert client.get("/api/v1/media-assets?label=x", headers=user_h).status_code == 422
    assert client.get("/api/v1/media-assets?foo=1", headers=user_h).status_code == 422
    one = client.get(f"/api/v1/media-assets/{a['id']}", headers=user_h)
    assert one.status_code == 200 and one.json()["id"] == a["id"]
    again = client.get(f"/api/v1/media-assets/{a['id']}", headers={**user_h, "If-None-Match": one.headers["ETag"]})
    assert again.status_code == 304
    assert client.get("/api/v1/media-assets/med_nope", headers=user_h).status_code == 404


def test_content_download(client, user_h, mp4):
    a = upload(client, user_h, mp4).json()
    r = client.get(f"/api/v1/media-assets/{a['id']}/content", headers=user_h)
    assert r.status_code == 200 and r.content == mp4
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"].startswith("attachment") and a["id"] in r.headers["content-disposition"]
    assert client.get(f"/api/v1/media-assets/{a['id']}/content").status_code == 401


def test_delete_rules(client, user_h, admin_h, mp4, app, settings):
    a = upload(client, user_h, mp4).json()
    assert client.delete(f"/api/v1/media-assets/{a['id']}", headers=user_h).status_code == 403
    assert client.delete(f"/api/v1/media-assets/{a['id']}", headers=admin_h).status_code == 204
    assert stored(settings) == []
    assert client.get(f"/api/v1/media-assets/{a['id']}", headers=user_h).status_code == 404
    from app.models import MediaAsset
    with app.state.session_factory() as db:
        db.add(MediaAsset(id="med_sample1", origin="sample", label="original", file_path="s.mp4", sha256="0" * 64,
                          mime_type="video/mp4", file_size=1, consent_ref="demo"))
        db.commit()
    r = client.delete("/api/v1/media-assets/med_sample1", headers=admin_h)
    assert r.status_code == 409 and r.json()["code"] == "STATE_CONFLICT"
