from sqlalchemy import select

from app.core.security import verify_password
from app.models import Lab, User
from app.seed import run_seed

ENV = {"SEED_ADMIN_PASSWORD": "admin-pass-123", "SEED_USER_EMAIL": "Reviewer1@Lab.Local"}


def test_seed_creates_and_is_idempotent(app):
    out: list[str] = []
    run_seed(app.state.engine, ENV, out=out.append, rounds=4)
    with app.state.session_factory() as db:
        assert db.scalar(select(Lab).where(Lab.code == "deepfake")).id == "lab_deepfake"
        users = {u.email: u for u in db.scalars(select(User))}
        assert set(users) == {"admin@lab.local", "reviewer1@lab.local"}  # email chữ thường
        assert users["admin@lab.local"].role == "admin"
        assert verify_password("admin-pass-123", users["admin@lab.local"].password_hash)
        assert users["admin@lab.local"].id.startswith("usr_")
    text = "\n".join(out)
    assert "admin-pass-123" not in text  # mật khẩu do người dùng đặt thì không in ra
    assert "chỉ hiện một lần" in text  # user không có biến → mật khẩu ngẫu nhiên in một lần

    out2: list[str] = []
    run_seed(app.state.engine, ENV, out=out2.append, rounds=4)
    with app.state.session_factory() as db:
        assert db.query(Lab).count() == 4 and db.query(User).count() == 2
    assert "Lab thêm mới: 0" in out2 and "Ca Prompt Injection thêm mới: 0" in out2 and all("Đã có" in line for line in out2[2:])


def test_seed_does_not_override_admin_changes(app):
    run_seed(app.state.engine, ENV, out=lambda _: None, rounds=4)
    with app.state.session_factory() as db:
        lab = db.scalar(select(Lab).where(Lab.code == "poisoning"))
        lab.enabled, lab.status = 1, "simulated"
        db.commit()
    run_seed(app.state.engine, ENV, out=lambda _: None, rounds=4)
    with app.state.session_factory() as db:
        lab = db.scalar(select(Lab).where(Lab.code == "poisoning"))
        assert (lab.enabled, lab.status) == (1, "simulated")


def test_seed_rejects_short_password_and_missing_tables(app, tmp_path):
    import pytest
    from app.core.db import make_engine

    with pytest.raises(SystemExit):
        run_seed(app.state.engine, {"SEED_ADMIN_PASSWORD": "ngan"}, out=lambda _: None, rounds=4)
    empty = make_engine(tmp_path / "empty.db")
    with pytest.raises(SystemExit):
        run_seed(empty, {}, out=lambda _: None)
    empty.dispose()
