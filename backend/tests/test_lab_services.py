"""Validate/run của 4 stub, registry và tính nhất quán với catalog/ORM."""
from pathlib import Path

import pytest

from app.config import Settings
from app.models import PromptRun
from app.models.database import RESULT_KINDS as ORM_RESULT_KINDS
from app.services import base
from app.services.base import JobCancelled, LabValidationError, RunContext
from app.services.catalog import LAB_CATALOG
from app.services.guard import run_guard
from app.services.registry import SERVICES, get_service


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef")


def ctx_for(settings, timeout=5.0) -> RunContext:
    return RunContext(settings, "exp_test", Path("/tmp/storage"), timeout)


def errors_of(service, payload, settings):
    with pytest.raises(LabValidationError) as exc:
        service.validate(payload, settings)
    return {e["field"] for e in exc.value.errors}


# ---- registry / catalog / ORM ----

def test_registry_matches_catalog():
    assert set(SERVICES) == {item["code"] for item in LAB_CATALOG}
    assert get_service("nope") is None
    assert all(s.code == code for code, s in SERVICES.items())


def test_result_kinds_match_orm():
    assert base.RESULT_KINDS == ORM_RESULT_KINDS


def test_record_columns_exist_in_orm():
    orm_cols = {c.name for c in PromptRun.__table__.columns}
    allowed = set(base.RECORD_COLUMNS["prompt_runs"]) - {"case_code"}
    assert allowed <= orm_cols
    # service không được tự đặt khóa/định danh/thời gian
    assert not {"id", "experiment_id", "case_id", "created_at"} & base.RECORD_COLUMNS["prompt_runs"]


# ---- stub trả kết quả qua guard với đúng trần của Lab ----

CASES = {
    "deepfake": {"asset_id": "med_abc123"},
    "prompt_injection": {"case_codes": ["PI-01", "PI-02"]},
    "api_lab": {"scenario_code": "burst-30"},
}


@pytest.mark.parametrize("code", sorted(CASES))
def test_stub_result_passes_guard_and_is_simulated(settings, code):
    svc = get_service(code)
    data = svc.validate(CASES[code], settings)
    result = svc.run(ctx_for(settings), data)
    assert result.result_kind == "simulated"
    assert run_guard(result, "simulated", settings).passed
    # một stub không được vượt trần planned
    assert not run_guard(result, "planned", settings).passed


def test_run_emits_events_and_respects_deadline(settings):
    svc = get_service("api_lab")
    ctx = ctx_for(settings)
    svc.run(ctx, svc.validate(CASES["api_lab"], settings))
    assert ctx.events and ctx.events[0]["event_type"] == "stub"
    with pytest.raises(JobCancelled):
        svc.run(ctx_for(settings, timeout=-1), svc.validate(CASES["api_lab"], settings))


def test_ctx_caps_events():
    s = Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", max_events_per_experiment=3)
    ctx = RunContext(s, "exp_x", Path("/tmp"), 5)
    for i in range(10):
        ctx.emit("weird-level", "t", "m" * 1000)
    assert len(ctx.events) == 3
    assert ctx.events[0]["level"] == "info" and len(ctx.events[0]["message"]) == 500


# ---- deepfake ----

def test_deepfake_defaults_and_limits(settings):
    svc = get_service("deepfake")
    assert svc.validate({"asset_id": "med_x1"}, settings) == {"asset_id": "med_x1", "frame_limit": settings.max_frames}
    assert errors_of(svc, {"asset_id": "med_x1", "frame_limit": settings.max_frames + 1}, settings) == {"frame_limit"}
    assert errors_of(svc, {"asset_id": "med_x1", "frame_limit": True}, settings) == {"frame_limit"}
    assert errors_of(svc, {"asset_id": "med_x1", "frame_limit": 0}, settings) == {"frame_limit"}


@pytest.mark.parametrize("asset", [None, "", "x", "ast_1", "med_", "med_../x", "med_a/b", "http://evil", 5])
def test_deepfake_rejects_bad_asset_id(settings, asset):
    assert "asset_id" in errors_of(get_service("deepfake"), {"asset_id": asset}, settings)


def test_deepfake_rejects_unknown_keys_and_non_object(settings):
    svc = get_service("deepfake")
    assert "url" in errors_of(svc, {"asset_id": "med_x1", "url": "http://evil"}, settings)
    assert errors_of(svc, ["med_x1"], settings) == {""}


def test_deepfake_stub_has_fixed_warnings_and_no_verdict(settings):
    svc = get_service("deepfake")
    res = svc.run(ctx_for(settings), svc.validate(CASES["deepfake"], settings))
    text = " ".join(res.limitations)
    assert "not_integrated" in text and "không phải kết luận chắc chắn" in text.lower()
    assert not {"verdict", "suspicion_score"} & set(res.metrics)


# ---- prompt injection ----

def test_pi_defaults(settings):
    out = get_service("prompt_injection").validate({"case_codes": ["PI-01"]}, settings)
    assert out == {"case_codes": ["PI-01"], "defenses": [False, True], "repeat": 1}


@pytest.mark.parametrize(
    "payload,field",
    [
        ({}, "case_codes"),
        ({"case_codes": []}, "case_codes"),
        ({"case_codes": ["PI-01", "PI-01"]}, "case_codes"),
        ({"case_codes": ["http://evil"]}, "case_codes"),
        ({"case_codes": [["x"]]}, "case_codes"),
        ({"case_codes": ["PI-01"], "defenses": []}, "defenses"),
        ({"case_codes": ["PI-01"], "defenses": [1, 0]}, "defenses"),
        ({"case_codes": ["PI-01"], "defenses": [True, True]}, "defenses"),
        ({"case_codes": ["PI-01"], "repeat": 6}, "repeat"),
        ({"case_codes": ["PI-01"], "repeat": 0}, "repeat"),
        ({"case_codes": ["PI-01"], "repeat": True}, "repeat"),
        ({"case_codes": ["PI-01"], "url": "http://evil"}, "url"),
    ],
)
def test_pi_rejects(settings, payload, field):
    assert field in errors_of(get_service("prompt_injection"), payload, settings)


def test_pi_call_cap(settings):
    svc = get_service("prompt_injection")
    cases = [f"PI-{i:02d}" for i in range(7)]  # 7 ca * 2 nhánh * 5 = 70 > 60
    assert errors_of(svc, {"case_codes": cases, "repeat": 5}, settings) == {"repeat"}
    assert svc.validate({"case_codes": cases[:6], "repeat": 5}, settings)  # đúng 60 lượt


# ---- api lab ----

def test_api_lab_defaults_and_rejects(settings):
    svc = get_service("api_lab")
    assert svc.validate({"scenario_code": "burst-30"}, settings)["rate_limit_modes"] == [False, True]
    for bad in ("http://127.0.0.1/x", "a/b", "", None, "x" * 40, "has space"):
        assert "scenario_code" in errors_of(svc, {"scenario_code": bad}, settings)
    assert "rate_limit_modes" in errors_of(svc, {"scenario_code": "burst-30", "rate_limit_modes": ["off", "on"]}, settings)
    assert "url" in errors_of(svc, {"scenario_code": "burst-30", "url": "http://evil"}, settings)


# ---- poisoning ----

def test_poisoning_is_not_runnable(settings):
    assert errors_of(get_service("poisoning"), {}, settings) == {""}
