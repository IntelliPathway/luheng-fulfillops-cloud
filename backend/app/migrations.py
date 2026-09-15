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


def run_sqlite_compatibility_migrations(engine: Engine) -> list[str]:
    """Apply additive development-only columns to an existing SQLite database.

    PostgreSQL remains governed by the versioned SQL files. SQLite is the local
    zero-dependency path, so additive columns are applied after create_all to
    preserve developer data across releases without pretending to be a full
    production migration engine.
    """

    if engine.dialect.name != "sqlite":
        return []
    additions = {
        "model_replay_runs": {
            "model_config_version": "integer",
            "model_name": "varchar(120)",
            "policy_snapshot": "json NOT NULL DEFAULT '{}'",
            "external_call_count": "integer NOT NULL DEFAULT 0",
            "input_tokens": "integer NOT NULL DEFAULT 0",
            "output_tokens": "integer NOT NULL DEFAULT 0",
            "estimated_cost_usd": "float NOT NULL DEFAULT 0",
        },
        "asset_packages": {
            "min_settlement_bps": "integer NOT NULL DEFAULT 7000",
            "max_installments": "integer NOT NULL DEFAULT 6",
            "min_down_payment_bps": "integer NOT NULL DEFAULT 2000",
        },
        "case_financial_profiles": {
            "claim_balance_cents": "integer NOT NULL DEFAULT 1",
        },
    }
    applied: list[str] = []
    with engine.begin() as connection:
        tables = set(connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).scalars())
        for table, columns_to_add in additions.items():
            if table not in tables:
                continue
            columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, definition in columns_to_add.items():
                if name in columns:
                    continue
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                applied.append(name)
    return applied
