import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all ORM models so autogenerate can detect them
from services.api.models.base import Base  # noqa: F401
from services.api.models.job import Job  # noqa: F401
from services.api.models.correction import Correction  # noqa: F401
from services.api.models.audit import AuditLog  # noqa: F401
from services.api.models.model_registry import ModelRegistry  # noqa: F401
from services.api.models.admin import AdminRoleGrant, DeploymentLog  # noqa: F401

target_metadata = Base.metadata

# Override sqlalchemy.url from env vars so secrets never touch alembic.ini
def get_url() -> str:
    import re
    override = os.environ.get("DATABASE_URL_OVERRIDE", "")
    if override:
        if override.startswith("postgresql://"):
            override = override.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg does not accept sslmode or channel_binding as query params — strip them
        override = re.sub(r"[?&]sslmode=[^&]*", "", override)
        override = re.sub(r"[?&]channel_binding=[^&]*", "", override)
        # Clean up dangling ? or & after stripping
        override = re.sub(r"\?&", "?", override)
        override = re.sub(r"\?$", "", override)
        return override
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "neondb")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Set the URL in the config object FIRST so ConfigParser never attempts
    # to interpolate the %(VAR)s placeholder that lives in alembic.ini.
    config.set_main_option("sqlalchemy.url", get_url())
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()  # belt-and-suspenders override
    # Neon requires SSL — pass ssl=True via connect_args since sslmode is stripped from URL
    use_ssl = "neon.tech" in get_url()
    connectable = async_engine_from_config(
        cfg, prefix="sqlalchemy.", poolclass=pool.NullPool,
        connect_args={"ssl": True} if use_ssl else {},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
