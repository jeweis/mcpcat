"""Alembic 运行环境。"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, event, pool

from alembic import context
from app.storage import app_models  # noqa: F401 - 注册 Apps metadata
from app.storage import skill_variant_models  # noqa: F401 - 注册变体 metadata
from app.storage import wecom_transactions  # noqa: F401 - 注册企微 metadata
from app.storage.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """在无数据库连接时生成迁移 SQL。"""

    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连接数据库并执行迁移。"""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # SQLite legacy transaction mode 不会为 DDL 自动 BEGIN；显式启用事务，
    # 确保新增多表或回填中途失败时可以完整回滚，而不留下半迁移表。
    @event.listens_for(connectable, "connect")
    def disable_legacy_transaction(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(connectable, "begin")
    def begin_transaction(connection):
        connection.exec_driver_sql("BEGIN")

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
