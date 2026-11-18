import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import BASE_DIR
from app.core.db import Base
from app.models import Lab, User


def _cfg(conn):
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    cfg.attributes["connection"] = conn
    return cfg


def test_upgrade_downgrade_upgrade_and_orm_matches(app):
    eng = app.state.engine
    assert {"users", "labs", "experiments", "lab_results", "experiment_events",
            "media_assets", "prompt_cases", "prompt_runs"} <= set(inspect(eng).get_table_names())
    with eng.begin() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff
    with eng.begin() as conn:
        command.downgrade(_cfg(conn), "base")
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}
    with eng.begin() as conn:
        command.upgrade(_cfg(conn), "head")
    assert "users" in inspect(eng).get_table_names()


def test_check_constraints_reject_bad_values(app):
    with app.state.session_factory() as db:
        db.add(Lab(code="x", name="x", service_key="x", status="active"))  # không thuộc real/simulated/planned
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):  # email phải chữ thường: bỏ qua validator ORM bằng SQL thô
            db.execute(text("INSERT INTO users (id,email,password_hash,full_name,role,status,created_at,updated_at) "
                            "VALUES ('u','A@B.C','h','n','user','active','t','t')"))
        db.rollback()
        db.add(User(email="  MixedCase@Lab.Local ", password_hash="h", full_name="n"))
        db.commit()
        assert db.query(User).one().email == "mixedcase@lab.local"
