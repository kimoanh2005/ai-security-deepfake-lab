"""T-14/T-17: kiểm tra hợp đồng xuyên suốt (API-01), tự phát hiện khi ai đó thêm endpoint làm sai quy ước.

Khác với test từng endpoint: ở đây duyệt toàn bộ route của app, nên endpoint mới thêm sau này
(artifacts, prompt-runs, cancel…) tự bị kiểm mà không phải sửa tệp này.
"""
from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from tests.test_experiments_api import API, PAYLOAD, h, seeded  # noqa: F401  (fixture dùng chung)

PUBLIC = {("POST", "/api/v1/auth/login"), ("GET", "/api/v1/health")}
ADMIN_ONLY = [("PATCH", "/api/v1/labs/api_lab", {"enabled": False}), ("DELETE", "/api/v1/media-assets/med_x", None)]
PROBLEM_KEYS = {"type", "title", "status", "detail", "code"}


def api_routes(app):
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, r.path, r


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def assert_problem(r, status, code=None):
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json"), r.headers["content-type"]
    body = r.json()
    assert PROBLEM_KEYS <= set(body), body
    assert body["status"] == status
    assert re.fullmatch(r"[A-Z][A-Z0-9_]+", body["code"]), body["code"]
    if code:
        assert body["code"] == code
    return body


# ---- xác thực / phân quyền ----

def test_there_are_routes_to_check(app):
    assert len(list(api_routes(app))) >= 15  # chống test rỗng nếu cách duyệt route hỏng


def test_every_non_public_route_requires_a_token(client, app):
    checked = 0
    for method, path, _ in api_routes(app):
        if (method, path) in PUBLIC:
            continue
        r = client.request(method, concrete(path), json={} if method in ("POST", "PUT", "PATCH") else None)
        assert_problem(r, 401, "UNAUTHENTICATED")
        assert r.headers.get("WWW-Authenticate") == "Bearer", (method, path)
        checked += 1
    assert checked >= 12


def test_garbage_and_wrong_scheme_tokens_are_401(client):
    for headers in ({"Authorization": "Bearer not.a.jwt"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}):
        assert_problem(client.get(f"{API}/labs", headers=headers), 401, "UNAUTHENTICATED")


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_admin_only_routes_reject_normal_user(client, seeded, h, method, path, body):
    r = client.request(method, path, headers=h, json=body)
    assert_problem(r, 403, "FORBIDDEN")


# ---- dạng lỗi thống nhất ----

def test_error_shapes_are_problem_json(client, h):
    assert_problem(client.get(f"{API}/no-such-resource", headers=h), 404, "NOT_FOUND")
    assert_problem(client.get(f"{API}/labs/nope", headers=h), 404, "NOT_FOUND")
    assert_problem(client.get(f"{API}/experiments/exp_nope", headers=h), 404, "NOT_FOUND")
    assert_problem(client.delete(f"{API}/labs", headers=h), 405, "METHOD_NOT_ALLOWED")
    assert_problem(client.post(f"{API}/auth/login", content=b"{bad", headers={"Content-Type": "application/json"}), 400, "MALFORMED_REQUEST")
    assert_problem(client.post(f"{API}/auth/login", json={"email": "x"}), 422, "VALIDATION_ERROR")
    assert_problem(client.get(f"{API}/experiments/exp_x/report", headers={**h, "Accept": "image/png"}), 406)


def test_validation_errors_list_fields_and_never_echo_input(client, h):
    body = assert_problem(client.post(f"{API}/auth/login", json={"email": "a@b.c", "password": 123456789}), 422, "VALIDATION_ERROR")
    assert body["errors"] and all({"field", "message"} <= set(e) for e in body["errors"])
    assert "123456789" not in str(body)


def test_unhandled_error_does_not_leak_details(app, client, h, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("secret-internal-detail")
    monkeypatch.setattr("app.api.v1.labs._get_lab", boom)
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c:
        body = assert_problem(c.get(f"{API}/labs/api_lab", headers=h), 500, "INTERNAL_ERROR")
    assert "secret-internal-detail" not in str(body) and "Traceback" not in str(body)


# ---- danh sách và truy vấn ----

LIST_ENDPOINTS = ["/labs", "/experiments", "/media-assets"]


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_collections_have_standard_envelope_and_paging_rules(client, seeded, h, path):
    r = client.get(API + path, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "page", "page_size", "total"}
    assert isinstance(body["items"], list) and body["page"] == 1
    for q in ("page=0", "page_size=0", "page_size=101", "page=abc"):
        assert_problem(client.get(f"{API}{path}?{q}", headers=h), 422, "VALIDATION_ERROR")
    assert_problem(client.get(f"{API}{path}?zzz=1", headers=h), 422, "VALIDATION_ERROR")


# ---- OpenAPI khớp hợp đồng ----

def test_openapi_has_unique_operation_ids_and_documented_endpoints(client):
    spec = client.get("/openapi.json").json()
    ids = [op["operationId"] for p in spec["paths"].values() for m, op in p.items() if isinstance(op, dict) and "operationId" in op]
    assert len(ids) == len(set(ids))
    expected = {
        ("post", "/api/v1/auth/login"), ("get", "/api/v1/users/me"), ("get", "/api/v1/health"),
        ("get", "/api/v1/labs"), ("get", "/api/v1/labs/{code}"), ("patch", "/api/v1/labs/{code}"),
        ("post", "/api/v1/labs/{code}/experiments"), ("get", "/api/v1/experiments"), ("get", "/api/v1/experiments/{exp_id}"),
        ("get", "/api/v1/experiments/{exp_id}/events"), ("get", "/api/v1/experiments/{exp_id}/report"),
        ("post", "/api/v1/media-assets"), ("get", "/api/v1/media-assets"), ("get", "/api/v1/media-assets/{media_id}"),
        ("get", "/api/v1/media-assets/{media_id}/content"), ("delete", "/api/v1/media-assets/{media_id}"),
        ("get", "/sandbox/echo"), ("post", "/sandbox/echo"),
    }
    present = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert expected <= present, sorted(expected - present)


def test_resource_paths_follow_naming_rules(app):
    """Danh từ số nhiều, chữ thường, kebab-case (API-01 mục 3); chỉ `auth/login` là động từ."""
    for _m, path, _ in api_routes(app):
        for seg in path.removeprefix("/api/v1").split("/"):
            if seg and not seg.startswith("{"):
                assert re.fullmatch(r"[a-z]+(-[a-z]+)*|users|me|login|health|auth|content|events|report", seg), (path, seg)
                assert "_" not in seg, (path, seg)


def test_every_response_has_nosniff(client, h):
    for r in (client.get(f"{API}/labs", headers=h), client.get(f"{API}/labs"), client.get(f"{API}/nope")):
        assert r.headers["X-Content-Type-Options"] == "nosniff"


# ---- luồng đầy đủ như frontend dùng ----

def test_full_flow_login_run_poll_result_report(client, app, seeded, make_user):
    email, pw = make_user("flow@lab.local", "flow-pass-123")
    token = client.post(f"{API}/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}

    r = client.post(f"{API}/labs/api_lab/experiments", json=PAYLOAD, headers=hdr)
    assert r.status_code == 202 and r.headers["Location"].startswith(f"{API}/experiments/exp_")
    assert int(r.headers["Retry-After"]) >= 1
    assert app.state.orchestrator.wait_idle(10)

    exp = client.get(r.headers["Location"], headers=hdr).json()
    assert exp["state"] == "done"
    assert exp["result_kind"] in ("real", "simulated", "planned")
    result = exp["result"]
    assert result["limitations"] and result["components"]
    assert not {"probability", "accuracy", "confidence_pct"} & set(result)  # nhãn trung thực (D-10, G4)

    events = client.get(f"{r.headers['Location']}/events", headers=hdr).json()
    assert [e["event_type"] for e in events["items"]][:1] == ["queued"] and events["total"] >= 2
    md = client.get(f"{r.headers['Location']}/report", headers={**hdr, "Accept": "text/markdown"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    js = client.get(f"{r.headers['Location']}/report", headers={**hdr, "Accept": "application/json"})
    assert js.status_code == 200 and "experiment" in js.json()

    etag = client.get(r.headers["Location"], headers=hdr).headers["ETag"]
    assert client.get(r.headers["Location"], headers={**hdr, "If-None-Match": etag}).status_code == 304
