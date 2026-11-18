from logging.config import fileConfig

from alembic import context

from app.config import get_settings
from app.core.db import Base, make_engine
import app.models  # noqa: F401  (để Alembic thấy các bảng)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=f"sqlite:///{get_settings().db_file}",
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,  # SQLite không sửa cột trực tiếp được
    )
    with context.begin_transaction():
        context.run_migrations()


def _migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Test truyền sẵn kết nối qua config.attributes["connection"]; chạy bình thường thì dùng DB_PATH trong .env.
    connection = config.attributes.get("connection")
    if connection is not None:
        _migrate(connection)
        return
    engine = make_engine(get_settings().db_file)
    try:
        with engine.connect() as conn:
            _migrate(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
