"""result_guard G1–G7 (SPEC 6.1) và các kiểm tra bổ sung."""
from dataclasses import replace

import pytest

from app.config import Settings
from app.services.base import Component, LabResult
from app.services.guard import run_guard


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", max_result_bytes=2048)


def good(**over) -> LabResult:
    base = LabResult(
        result_kind="simulated",
        summary="ok",
        components=[Component("a", "simulated")],
        limitations=["giới hạn"],
        metrics={"n": 1},
    )
    return replace(base, **over)


def verdict(result, ceiling="simulated", settings=None):
    return run_guard(result, ceiling, settings or Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef"))


def test_good_result_passes(settings):
    rep = verdict(good(), settings=settings)
    assert rep.passed, rep.failed
    assert rep.as_dict()["passed"] is True


def test_not_a_lab_result_is_rejected_without_raising(settings):
    for bad in (None, {"result_kind": "real"}, "x", good(metrics=[1])):
        assert not verdict(bad, settings=settings).passed


def test_g1_invalid_kind(settings):
    rep = verdict(good(result_kind="ok"), settings=settings)
    assert "G1_kind_valid" in rep.failed


@pytest.mark.parametrize(
    "ceiling,kind,ok",
    [("simulated", "real", False), ("planned", "simulated", False), ("planned", "real", False),
     ("real", "real", True), ("real", "simulated", True), ("simulated", "planned", True)],
)
def test_g2_ceiling(settings, ceiling, kind, ok):
    rep = verdict(good(result_kind=kind, components=[Component("a", kind)]), ceiling, settings)
    assert ("G2_within_lab_ceiling" not in rep.failed) is ok


def test_g2_unknown_ceiling_is_rejected(settings):
    assert "G2_within_lab_ceiling" in verdict(good(), "weird", settings).failed


@pytest.mark.parametrize("lim", [[], None, "text", [""], ["  "], [1]])
def test_g3_limitations_required(settings, lim):
    assert "G3_limitations" in verdict(good(limitations=lim), settings=settings).failed


@pytest.mark.parametrize("key", ["probability", "accuracy", "confidence_pct"])
def test_g4_forbidden_keys(settings, key):
    assert "G4_no_misleading_metrics" in verdict(good(metrics={key: 0.97}), settings=settings).failed


def test_g4_allowed_when_calibrated(settings):
    assert verdict(good(metrics={"accuracy": 0.9, "calibrated": True}), settings=settings).passed


def test_g4_calibrated_must_be_exactly_true(settings):
    rep = verdict(good(metrics={"accuracy": 0.9, "calibrated": "yes"}), settings=settings)
    assert "G4_no_misleading_metrics" in rep.failed


def test_g5_kind_must_be_weakest_component(settings):
    comps = [Component("preprocess", "real"), Component("detector", "planned")]
    assert "G5_kind_is_weakest" in verdict(good(result_kind="real", components=comps), "real", settings).failed
    assert verdict(good(result_kind="planned", components=comps), "real", settings).passed


def test_g5_components_required_and_valid(settings):
    assert "G5_kind_is_weakest" in verdict(good(components=[]), settings=settings).failed
    assert "G5_kind_is_weakest" in verdict(good(components=[Component("a", "fake")]), settings=settings).failed
    assert "G5_kind_is_weakest" in verdict(good(components=[Component("", "simulated")]), settings=settings).failed


@pytest.mark.parametrize("path", ["../../etc/x", "/etc/passwd", "a/../../b", "C:\\x", "a\\b", "", "  ", "a\x00b"])
def test_g6_unsafe_artifacts(settings, path):
    assert "G6_artifacts_relative" in verdict(good(artifacts=[path]), settings=settings).failed


def test_g6_safe_artifact(settings):
    assert verdict(good(artifacts=["experiments/exp_1/frames/000001.jpg"]), settings=settings).passed


def test_g7_size_limit(settings):
    assert "G7_serializable_and_size" in verdict(good(metrics={"blob": "x" * 5000}), settings=settings).failed


@pytest.mark.parametrize("metrics", [{"x": object()}, {"x": float("nan")}, {"x": {1, 2}}])
def test_g7_not_json(settings, metrics):
    assert "G7_serializable_and_size" in verdict(good(metrics=metrics), settings=settings).failed


def test_records_unknown_table_or_column(settings):
    assert "records" in verdict(good(records={"users": [{"email": "a"}]}), settings=settings).failed
    assert "records" in verdict(good(records={"prompt_runs": [{"id": "x"}]}), settings=settings).failed
    assert "records" in verdict(good(records={"prompt_runs": "no"}), settings=settings).failed


def test_records_valid_unjudged_run(settings):
    row = {"case_code": "PI-01", "attempt": 1, "defense": 0, "response": "r", "latency_ms": 5, "model_name": "m"}
    assert verdict(good(records={"prompt_runs": [row]}), settings=settings).passed


def test_records_service_cannot_self_assign_success(settings):
    base = {"case_code": "PI-01", "attempt": 1, "defense": 0}
    # đặt kết quả mà không phải do quy tắc chấm
    for extra in ({"task_violated": 1}, {"task_violated": 0, "judged_by": "human"}, {"judged_by": "rule"}):
        rep = verdict(good(records={"prompt_runs": [{**base, **extra}]}), settings=settings)
        assert "records" in rep.failed, extra
    ok = {**base, "task_violated": 1, "judged_by": "rule"}
    assert verdict(good(records={"prompt_runs": [ok]}), settings=settings).passed
