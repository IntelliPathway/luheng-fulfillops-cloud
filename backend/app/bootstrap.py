from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from .config import StartupSettings
from .db import Base
from .migrations import run_postgres_migrations, run_sqlite_compatibility_migrations
from .seed import seed_demo_data


@dataclass(frozen=True)
class BootstrapResult:
    applied_postgres_migrations: tuple[str, ...]
    applied_sqlite_columns: tuple[str, ...]
    demo_data_seeded: bool


def bootstrap_database(
    engine: Engine,
    session_factory: sessionmaker,
    settings: StartupSettings,
) -> BootstrapResult:
    """Initialize storage without allowing ORM schema drift in production."""

    postgres_migrations = tuple(run_postgres_migrations(engine))
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)
    sqlite_columns = tuple(run_sqlite_compatibility_migrations(engine)) if settings.auto_create_schema else ()
    if settings.seed_demo_data:
        with session_factory() as db:
            seed_demo_data(db)
    return BootstrapResult(
        applied_postgres_migrations=postgres_migrations,
        applied_sqlite_columns=sqlite_columns,
        demo_data_seeded=settings.seed_demo_data,
    )
