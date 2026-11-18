import logging

LABS = "/api/v1/labs"


def _user_headers(make_user, login):
    return login(*make_user())


def _admin_headers(make_user, login):
    return login(*make_user(email="admin@lab.local", password="admin-pass-123", role="admin"))


def _seed(app):
    from app.seed import ensure_labs
    with app.state.session_factory() as db:
        ensure_labs(db)


def test_labs_require_auth(client, app):
    _seed(app)
    assert client.get(LABS).status_code == 401
    assert client.get(f"{LABS}/deepfake").status_code == 401
    assert client.patch(f"{LABS}/deepfake", json={"enabled": False}).status_code == 401


def test_list_labs_from_db_shape(client, app, make_user, login):
    _seed(app)
    r = client.get(LABS, headers=_user_headers(make_user, login))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "page", "page_size", "total"}
    assert body["total"] == 4 and body["page"] == 1 and body["page_size"] == 20
    by_code = {i["code"]: i for i in body["items"]}
    assert list(by_code) == ["deepfake", "prompt_injection", "api_lab", "poisoning"]
    assert by_code["poisoning"]["status"] == "planned" and by_code["poisoning"]["enabled"] is False
    assert {i["status"] for i in body["items"]} <= {"real", "simulated", "planned"}
    assert by_code["prompt_injection"]["id"] == 2  # id số tạm (deprecated)
    assert by_code["deepfake"]["links"] == {"self": "/api/v1/labs/deepfake"}
    assert r.headers["x-content-type-options"] == "nosniff"


def test_pagination_and_unknown_params(client, app, make_user, login):
    _seed(app)
    h = _user_headers(make_user, login)
    r = client.get(LABS, params={"page": 2, "page_size": 3}, headers=h).json()
    assert [i["code"] for i in r["items"]] == ["poisoning"] and r["total"] == 4
    assert client.get(LABS, params={"page_size": 101}, headers=h).status_code == 422
    assert client.get(LABS, params={"page": 0}, headers=h).status_code == 422
    r = client.get(LABS, params={"lab": "x"}, headers=h)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "lab"


def test_get_lab_and_404(client, app, make_user, login):
    _seed(app)
    h = _user_headers(make_user, login)
    assert client.get(f"{LABS}/api_lab", headers=h).json()["name"] == "API Security & Rate Limiting"
    r = client.get(f"{LABS}/khong-co", headers=h)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert r.headers["content-type"].startswith("application/problem+json")


def test_patch_requires_admin_and_validates(client, app, make_user, login):
    _seed(app)
    user_h = _user_headers(make_user, login)
    admin_h = _admin_headers(make_user, login)
    r = client.patch(f"{LABS}/deepfake", json={"enabled": False}, headers=user_h)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    # quyền được kiểm tra trước khi kiểm tra thân yêu cầu
    assert client.patch(f"{LABS}/deepfake", json={}, headers=user_h).status_code == 403
    assert client.patch(f"{LABS}/deepfake", json={}, headers=admin_h).status_code == 422
    assert client.patch(f"{LABS}/deepfake", json={"status": "active"}, headers=admin_h).status_code == 422
    assert client.patch(f"{LABS}/deepfake", json={"name": "x"}, headers=admin_h).status_code == 422
    assert client.patch(f"{LABS}/nope", json={"enabled": True}, headers=admin_h).status_code == 404


def test_patch_updates_and_logs(client, app, make_user, login, caplog):
    _seed(app)
    admin_h = _admin_headers(make_user, login)
    with caplog.at_level(logging.INFO, logger="app.security"):
        r = client.patch(f"{LABS}/deepfake", json={"enabled": False, "status": "planned"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["status"] == "planned"
    assert any("lab_toggled" in rec.getMessage() and "deepfake" in rec.getMessage() for rec in caplog.records)
    assert client.get(f"{LABS}/deepfake", headers=admin_h).json()["enabled"] is False  # đã lưu


def test_method_not_allowed_has_allow_and_problem_json(client, make_user, login, app):
    _seed(app)
    h = _user_headers(make_user, login)
    r = client.delete(f"{LABS}/deepfake", headers=h)
    assert r.status_code == 405 and "allow" in r.headers and r.json()["code"] == "METHOD_NOT_ALLOWED"


def test_cors_allows_only_configured_origin(client):
    ok = client.options("/api/v1/labs", headers={"Origin": "http://localhost:5173",
                                                 "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/v1/labs", headers={"Origin": "http://evil.example",
                                                  "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
    r = client.get("/", headers={"Origin": "http://localhost:5173"})
    assert "location" in r.headers.get("access-control-expose-headers", "").lower()
