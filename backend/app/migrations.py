from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import Engine, text

_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _split_postgres_statements(source: str) -> list[str]:
    """Split SQL without breaking strings, comments, or dollar-quoted blocks."""

    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_quote: str | None = None
    block_comment_depth = 0
    in_line_comment = False

    while index < len(source):
        if in_line_comment:
            if source[index] == "\n":
                in_line_comment = False
            index += 1
            continue

        if block_comment_depth:
            if source.startswith("/*", index):
                block_comment_depth += 1
                index += 2
            elif source.startswith("*/", index):
                block_comment_depth -= 1
                index += 2
            else:
                index += 1
            continue

        if dollar_quote:
            if source.startswith(dollar_quote, index):
                index += len(dollar_quote)
                dollar_quote = None
            else:
                index += 1
            continue

        if quote:
            if source[index] == "\\":
                index += 2
            elif source[index] == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    index += 2
                else:
                    quote = None
                    index += 1
            else:
                index += 1
            continue

        if source.startswith("--", index):
            in_line_comment = True
            index += 2
            continue
        if source.startswith("/*", index):
            block_comment_depth = 1
            index += 2
            continue
        if source[index] in {"'", '"'}:
            quote = source[index]
            index += 1
            continue
        if source[index] == "$":
            match = _DOLLAR_QUOTE.match(source, index)
            if match:
                dollar_quote = match.group(0)
                index = match.end()
                continue
        if source[index] == ";":
            statement = source[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1

    statement = source[start:].strip()
    if statement:
        statements.append(statement)
    return statements


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
            for statement in _split_postgres_statements(path.read_text(encoding="utf-8")):
                connection.exec_driver_sql(statement)
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"), {"version": path.name}
            )
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
            "source_import_batch_id": "varchar(40)",
            "created_at": "timestamp NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
        "cases": {
            "contact_basis_ref": "varchar(120)",
            "source_import_batch_id": "varchar(40)",
            "version": "integer NOT NULL DEFAULT 1",
            "created_at": "timestamp NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
        "case_financial_profiles": {
            "claim_balance_cents": "integer NOT NULL DEFAULT 1",
            "principal_cents": "integer",
            "interest_cents": "integer",
            "fee_cents": "integer",
            "first_overdue_date": "date",
            "last_contact_at": "timestamp",
        },
        "contact_attempts": {
            "retry_of_id": "varchar(40)",
            "cancelled_by": "varchar(80)",
            "cancelled_at": "timestamp",
            "cancel_reason": "text",
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
