from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, text


def _migration_files() -> list[Path]:
    return sorted((Path(__file__).resolve().parents[1] / "migrations").glob("[0-9][0-9][0-9]_*.sql"))


def run_postgres_migrations(engine: Engine) -> list[str]:
    """Apply repository PostgreSQL migrations exactly once.

    SQLite remains a zero-dependency test/development path and is built from
    SQLAlchemy metadata. PostgreSQL upgrades are transactionally recorded so an
    existing v0.4 data volume can safely start the current API and worker.
    """

    if engine.dialect.name != "postgresql":
        return []
    applied_now: list[str] = []
    with engine.begin() as connection:
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('luheng-fulfillops-migrations'))")
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version varchar(255) PRIMARY KEY, applied_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = set(connection.execute(text("SELECT version FROM schema_migrations")).scalars())
        for path in _migration_files():
            if path.name in applied:
                continue
            statements = [statement.strip() for statement in path.read_text(encoding="utf-8").split(";")]
            for statement in statements:
                if statement:
                    connection.exec_driver_sql(statement)
            connection.execute(text("INSERT INTO schema_migrations (version) VALUES (:version)"), {"version": path.name})
            applied_now.append(path.name)
    return applied_now
