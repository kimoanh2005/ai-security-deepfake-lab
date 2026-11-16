import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")  # admin / user
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # active / disabled
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso, onupdate=now_iso)


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False)
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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id"), unique=True, nullable=False
    )  # quan hệ 1-1
    result_kind: Mapped[str] = mapped_column(String, nullable=False)  # real / simulated / planned
    result_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # không rỗng khi guard_passed=1
    guard_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 / 1
    guard_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class ExperimentEvent(Base):
    __tablename__ = "experiment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # tăng dần, sắp thứ tự
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False, default="info")  # info / warn / error
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_json: Mapped[dict | None] = mapped_column(JSON)  # tùy chọn
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)

class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))  # NULL nếu là mẫu có sẵn
    origin: Mapped[str] = mapped_column(String, nullable=False)  # sample / upload
    label: Mapped[str] = mapped_column(String, nullable=False)  # original / synthetic
    file_path: Mapped[str] = mapped_column(String, nullable=False)  # tên ngẫu nhiên, tương đối trong storage/
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str | None] = mapped_column(String)
    consent_ref: Mapped[str] = mapped_column(String, nullable=False)  # NOT NULL theo ER
    technique: Mapped[str | None] = mapped_column(String)
    meta_json: Mapped[dict | None] = mapped_column(JSON)  # duration, fps, size, codec
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class PromptCase(Base):
    __tablename__ = "prompt_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # PI-01...
    task: Mapped[str] = mapped_column(Text, nullable=False)
    document_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)


class PromptRun(Base):
    __tablename__ = "prompt_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("prompt_cases.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    defense: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 / 1
    response: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    model_name: Mapped[str | None] = mapped_column(String)
    task_violated: Mapped[int | None] = mapped_column(Integer)  # NULL = chưa chấm
    judged_by: Mapped[str | None] = mapped_column(String)  # rule / human
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=now_iso)