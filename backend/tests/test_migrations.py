from app.migrations import _split_postgres_statements


def test_existing_loan_sessions_upgrade_without_granting_authorization():
    from sqlalchemy import create_engine, text

    from app.migrations import run_sqlite_compatibility_migrations
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE loan_sessions (id varchar(40) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO loan_sessions VALUES ('legacy')")
    run_sqlite_compatibility_migrations(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, authorization_expires_at, policy_version, policy_snapshot FROM loan_sessions")).one() == ("legacy", None, None, "{}")
    assert run_sqlite_compatibility_migrations(engine) == []


def test_split_postgres_statements_preserves_dollar_quoted_blocks() -> None:
    source = """
    CREATE TABLE example (id integer);
    DO $$ BEGIN
      ALTER TABLE example ADD CONSTRAINT positive_id CHECK (id > 0);
      RAISE NOTICE 'migration; complete';
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;
    INSERT INTO example VALUES (1);
    """

    statements = _split_postgres_statements(source)

    assert len(statements) == 3
    assert statements[0].startswith("CREATE TABLE")
    assert statements[1].startswith("DO $$ BEGIN")
    assert statements[1].endswith("END $$")
    assert statements[2] == "INSERT INTO example VALUES (1)"


def test_split_postgres_statements_handles_tags_quotes_and_comments() -> None:
    source = """
    -- a comment containing ;
    SELECT 'semi;colon', "quoted;identifier";
    DO $migration$ BEGIN
      PERFORM 'nested;value';
    END $migration$;
    /* outer ; /* nested ; */ done */ SELECT 2;
    """

    statements = _split_postgres_statements(source)

    assert len(statements) == 3
    assert statements[0].endswith("SELECT 'semi;colon', \"quoted;identifier\"")
    assert statements[1].startswith("DO $migration$ BEGIN")
    assert statements[2].endswith("SELECT 2")
