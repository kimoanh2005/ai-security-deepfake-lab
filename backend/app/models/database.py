import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.db import Base


def new_id(prefix: str = ""):
    """Trả về hàm sinh định danh mờ có tiền tố (vd exp_3f2a...), dùng làm `default` của cột id."""
    return lambda: f"{prefix}_{uuid.uuid4().hex}" if prefix else uuid.uuid4().hex


def now_iso() -> str:
    """ISO-8601 UTC, mili-giây, hậu tố Z (vd 2026-11-16T03:10:00.123Z) để sắp xếp ổn định."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _in(column: str, values: tuple) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


RESULT_KINDS = ("real", "simulated", "planned")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", ("admin", "user")), name="ck_users_role"),
        CheckConstraint(_in("status", ("active", "disabled")), name="ck_users_status"),
        CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("usr"))
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # luôn chữ thường
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")  # admin / user
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # active / disabled
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso, onupdate=now_iso)

    @validates("email")
    def _normalize_email(self, _key, value: str) -> str:
        return value.strip().lower()


class Lab(Base):
    __tablename__ = "labs"
    __table_args__ = (
        CheckConstraint(_in("status", RESULT_KINDS), name="ck_labs_status"),
        CheckConstraint("enabled IN (0, 1)", name="ck_labs_enabled"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("lab"))
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    service_key: Mapped[str] = mapped_column(String, nullable=False)  # khóa LabService
    status: Mapped[str] = mapped_column(String, nullable=False, default="simulated")  # real / simulated / planned
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso, onupdate=now_iso)


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(_in("state", ("queued", "running", "done", "failed")), name="ck_experiments_state"),
        Index("ix_experiments_lab_id_created_at", "lab_id", "created_at"),
        Index("ix_experiments_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("exp"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id", ondelete="RESTRICT"), nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="queued")  # queued / running / done / failed
    input_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # cấu hình đã làm sạch
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso, onupdate=now_iso)


class LabResult(Base):
    __tablename__ = "lab_results"
    __table_args__ = (
        CheckConstraint(_in("result_kind", RESULT_KINDS), name="ck_lab_results_result_kind"),
        CheckConstraint("guard_passed IN (0, 1)", name="ck_lab_results_guard_passed"),
        # Kết quả được chấp nhận thì limitations bắt buộc không rỗng (nguyên tắc nhãn trung thực)
        CheckConstraint(
            "guard_passed = 0 OR json_array_length(limitations) > 0",
            name="ck_lab_results_limitations_required",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("res"))
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), unique=True, nullable=False
    )  # quan hệ 1-1
    result_kind: Mapped[str] = mapped_column(String, nullable=False)  # real / simulated / planned
    result_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # không rỗng khi guard_passed=1
    guard_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 / 1
    guard_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class ExperimentEvent(Base):
    __tablename__ = "experiment_events"
    __table_args__ = (
        CheckConstraint(_in("level", ("info", "warn", "error")), name="ck_experiment_events_level"),
        Index("ix_experiment_events_experiment_id_id", "experiment_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # tăng dần, sắp thứ tự
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False, default="info")  # info / warn / error
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # tùy chọn; None → NULL
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint(_in("origin", ("sample", "upload")), name="ck_media_assets_origin"),
        CheckConstraint(_in("label", ("original", "synthetic")), name="ck_media_assets_label"),
        Index("ix_media_assets_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("med"))
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))  # NULL nếu là mẫu có sẵn
    origin: Mapped[str] = mapped_column(String, nullable=False)  # sample / upload
    label: Mapped[str] = mapped_column(String, nullable=False)  # original / synthetic
    file_path: Mapped[str] = mapped_column(String, nullable=False)  # tên ngẫu nhiên, tương đối trong storage/
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str | None] = mapped_column(String)
    consent_ref: Mapped[str] = mapped_column(String, nullable=False)  # NOT NULL theo ER
    technique: Mapped[str | None] = mapped_column(String)
    meta_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # duration, fps, size, codec
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class PromptCase(Base):
    __tablename__ = "prompt_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("case"))
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # PI-01...
    task: Mapped[str] = mapped_column(Text, nullable=False)
    document_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class PromptRun(Base):
    __tablename__ = "prompt_runs"
    __table_args__ = (
        CheckConstraint("attempt >= 1", name="ck_prompt_runs_attempt"),
        CheckConstraint("defense IN (0, 1)", name="ck_prompt_runs_defense"),
        # NULL = chưa chấm (không tự gán thành công)
        CheckConstraint("task_violated IS NULL OR task_violated IN (0, 1)", name="ck_prompt_runs_task_violated"),
        CheckConstraint("judged_by IS NULL OR judged_by IN ('rule', 'human')", name="ck_prompt_runs_judged_by"),
        Index("ix_prompt_runs_experiment_id", "experiment_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("run"))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("prompt_cases.id", ondelete="RESTRICT"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    defense: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 / 1
    response: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    model_name: Mapped[str | None] = mapped_column(String)
    task_violated: Mapped[int | None] = mapped_column(Integer)  # NULL = chưa chấm
    judged_by: Mapped[str | None] = mapped_column(String)  # rule / human
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class IdempotencyKey(Base):
    """Khóa chống trùng của POST /labs/{lab}/experiments (API-01 mục 8). Phạm vi theo người dùng."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_idempotency_keys_user_id_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id("idk"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)  # sha256 của (lab, thân yêu cầu)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
