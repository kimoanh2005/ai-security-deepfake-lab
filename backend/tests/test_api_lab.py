"""Lab API Security (tích hợp TV4): validate kịch bản, request thật qua MockTransport, 429, lỗi sandbox."""
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.services.base import LabRunError, LabValidationError, RunContext
from app.services.guard import run_guard
from app.services.labs.api_lab import ApiLabService, percentile


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", sandbox_token="tok-sandbox-0123456789",
                    sandbox_base_url="http://127.0.0.1:8123")


def ctx_for(settings, timeout=30.0) -> RunContext:
    return RunContext(settings, "exp_api1", Path("/tmp/storage"), timeout)


def service(handler) -> ApiLabService:
    svc = ApiLabService()
    svc.transport = httpx.MockTransport(handler)
    return svc


def go(svc, settings, payload):
    return svc.run(ctx_for(settings), svc.validate(payload, settings))


def test_validate_scenarios(settings):
    svc = ApiLabService()
    assert svc.validate({"scenario_code": "burst-30"}, settings)["rate_limit_modes"] == [False, True]
    for bad in ("burst-0", "burst-x", "slow-10", "burst-51"):  # 51 * 2 > 100
        with pytest.raises(LabValidationError) as exc:
            svc.validate({"scenario_code": bad}, settings)
        assert exc.value.errors[0]["field"] == "scenario_code"
    assert svc.validate({"scenario_code": "burst-100", "rate_limit_modes": [True]}, settings)  # 100 <= 100


def test_run_sends_contract_headers_and_counts(settings):
    seen, hits = [], {"on": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers["x-lab-rate-limit"] == "on":
            hits["on"] += 1
            if hits["on"] > 5:
                return httpx.Response(429, headers={"Retry-After": "3"}, json={})
        return httpx.Response(200, json={"ok": True})

    svc = service(handler)
    ctx = ctx_for(settings)
    res = svc.run(ctx, svc.validate({"scenario_code": "burst-8"}, settings))
    assert len(seen) == 16
    assert all(str(r.url) == "http://127.0.0.1:8123/sandbox/echo" for r in seen)
    assert all(r.headers["x-lab-token"] == "tok-sandbox-0123456789" and r.headers["x-lab-run"] == "exp_api1" for r in seen)
    off, on = res.metrics["rate_limit_off"], res.metrics["rate_limit_on"]
    assert (off["http_200"], off["http_429"]) == (8, 0) and (on["http_200"], on["http_429"]) == (5, 3)
    assert res.metrics["requests_sent"] == 16
    assert sum(e["event_type"] == "rate_limited" for e in ctx.events) == 3
    assert run_guard(res, "simulated", settings).passed
    assert not run_guard(res, "planned", settings).passed
    # không lộ token ở bất kỳ đâu trong kết quả/sự kiện
    assert "tok-sandbox" not in repr(res) and "tok-sandbox" not in repr(ctx.events)


def test_single_mode_only_reports_that_branch(settings):
    res = go(service(lambda r: httpx.Response(200, json={})), settings, {"scenario_code": "burst-3", "rate_limit_modes": [True]})
    assert "rate_limit_on" in res.metrics and "rate_limit_off" not in res.metrics


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(401, json={}),
    lambda r: httpx.Response(500),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused http://127.0.0.1:8123 tok")),
])
def test_sandbox_problem_is_dep_unavailable_without_leaks(settings, handler):
    with pytest.raises(LabRunError) as exc:
        go(service(handler), settings, {"scenario_code": "burst-3"})
    assert exc.value.code == "DEP_UNAVAILABLE"


def test_deadline_stops_before_sending(settings):
    sent = []
    svc = service(lambda r: sent.append(r) or httpx.Response(200, json={}))
    from app.services.base import JobCancelled
    with pytest.raises(JobCancelled):
        svc.run(ctx_for(settings, timeout=-1), svc.validate({"scenario_code": "burst-3"}, settings))
    assert sent == []


def test_percentile():
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2.5 and percentile([10], 0.95) == 10
