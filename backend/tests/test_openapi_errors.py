"""Tài liệu OpenAPI của lỗi phải khớp với thực tế (problem+json), không còn schema 422 mặc định của FastAPI."""
from __future__ import annotations

import json

import pytest

from app.core.errors import PROBLEM_JSON
from app.core.openapi import PROBLEM_REF, document_errors, problem_responses
from tests.test_contract import ADMIN_ONLY, PUBLIC, api_routes, assert_problem, concrete
from tests.test_experiments_api import API, h, seeded  # noqa: F401

NO_AUTH_BODY = {"POST", "PUT", "PATCH"}
NOT_RATE_LIMITED = {("GET", f"{API}/users/me")}  # API-01 4.3: hạn mức điều khiển chỉ áp cho labs/experiments/media-assets


@pytest.fixture()
def spec(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    return r.json()


def operations(spec):
    for path, item in spec["paths"].items():
        if path.startswith(API):
            for method, op in item.items():
                yield method.upper(), path, op


def collect_refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                yield v
            else:
                yield from collect_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from collect_refs(v)


def test_no_default_fastapi_validation_schema_remains(spec):
    assert "HTTPValidationError" not in json.dumps(spec)
    assert "ValidationError" not in spec["components"]["schemas"]


def test_every_error_response_is_problem_json(spec):
    seen = 0
    for method, path, op in operations(spec):
        for status, response in op["responses"].items():
            if int(status) < 400:
                continue
            assert list(response["content"]) == [PROBLEM_JSON], (method, path, status)
            assert response["content"][PROBLEM_JSON]["schema"] == {"$ref": PROBLEM_REF}
            assert response["description"], (method, path, status)
            seen += 1
    assert seen >= 40


def test_all_refs_resolve(spec):
    for ref in set(collect_refs(spec)):
        node = spec
        for part in ref.removeprefix("#/").split("/"):
            node = node[part]  # KeyError nếu tham chiếu treo
    assert {"ProblemDetails", "FieldError"} <= set(spec["components"]["schemas"])


def test_problem_details_schema_matches_runtime_body(spec, client):
    schema = spec["components"]["schemas"]["ProblemDetails"]
    r = client.get(f"{API}/users/me")
    body = assert_problem(r, 401)
    assert set(body) <= set(schema["properties"])
    assert {"type", "title", "status", "detail", "code"} == set(schema["required"])


def test_authenticated_routes_document_401_429_500(spec):
    checked = 0
    for method, path, op in operations(spec):
        if (method, path) in PUBLIC:
            continue
        got = set(op["responses"])
        assert {"401", "500"} <= got, (method, path)
        assert op["responses"]["401"]["headers"]["WWW-Authenticate"]
        if (method, path) in NOT_RATE_LIMITED:
            assert "429" not in got
            continue
        assert "Retry-After" in op["responses"]["429"]["headers"], (method, path)
        checked += 1
    assert checked >= 12


def test_public_routes_do_not_claim_401_unless_declared(spec):
    health = spec["paths"][f"{API}/health"]["get"]["responses"]
    assert "401" not in health and "429" not in health
    login = spec["paths"][f"{API}/auth/login"]["post"]["responses"]
    assert {"400", "401", "422", "429", "500"} <= set(login)


def test_admin_routes_document_403(spec):
    for method, path, _ in ADMIN_ONLY:
        template = next(p for p in spec["paths"] if p.startswith(API) and concrete(p) == path.replace("api_lab", "x").replace("med_x", "x"))
        assert "403" in spec["paths"][template][method.lower()]["responses"], (method, path)
    assert "403" not in spec["paths"][f"{API}/labs"]["get"]["responses"]


def test_path_param_routes_document_404_and_collections_do_not(spec):
    assert "404" in spec["paths"][f"{API}/experiments/{{exp_id}}"]["get"]["responses"]
    assert "404" not in spec["paths"][f"{API}/experiments"]["get"]["responses"]


def test_route_specific_errors_are_documented(spec):
    create = spec["paths"][f"{API}/labs/{{code}}/experiments"]["post"]["responses"]
    assert {"400", "401", "404", "409", "422", "429", "503"} <= set(create)
    assert "IDEMPOTENCY_KEY_REUSED" in create["422"]["description"] and "QUEUE_FULL" in create["503"]["description"]
    upload = spec["paths"][f"{API}/media-assets"]["post"]["responses"]
    assert {"400", "413", "415", "422", "503"} <= set(upload)
    assert "409" in spec["paths"][f"{API}/media-assets/{{media_id}}"]["delete"]["responses"]
    assert {"406", "409"} <= set(spec["paths"][f"{API}/experiments/{{exp_id}}/report"]["get"]["responses"])


def test_documented_statuses_include_the_real_ones(client, app, spec):
    """Mỗi mã lỗi thật mà test_contract kích hoạt được phải có trong tài liệu của đúng endpoint."""
    by_op = {(m, p): set(op["responses"]) for m, p, op in operations(spec)}
    for method, path, _ in api_routes(app):
        if (method, path) in PUBLIC:
            continue
        r = client.request(method, concrete(path), json={} if method in NO_AUTH_BODY else None)
        assert str(r.status_code) in by_op[(method, path)], (method, path, r.status_code)


def test_document_errors_is_idempotent(app):
    first = json.dumps(app.openapi(), sort_keys=True)
    document_errors(app.openapi(), app.routes)
    assert json.dumps(app.openapi(), sort_keys=True) == first


def test_problem_responses_helper_shape():
    out = problem_responses({409: "x"})
    assert out == {409: {"description": "x", "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_REF}}}}}
