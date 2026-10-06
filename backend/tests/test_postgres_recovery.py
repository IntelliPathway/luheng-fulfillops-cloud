"""Isolated full-schema pg_dump/restore rehearsal, never restores into an existing database."""

import os
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_customer_materials import headers, payload

from app.main import create_app


@pytest.mark.skipif(
    not (os.getenv("TEST_POSTGRES_URL") and os.getenv("PG_RECOVERY_CONTAINER")), reason="isolated PG container required"
)
def test_full_schema_backup_restores_encrypted_material_and_app_rollback(monkeypatch):
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    container = os.environ["PG_RECOVERY_CONTAINER"]
    source, target = ["recovery_" + uuid4().hex[:12] for _ in range(2)]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "recovery-test-v1")
    applications = []
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{source}"'))
        connection.execute(text(f'CREATE DATABASE "{target}"'))
    try:
        app = create_app(url.set(database=source).render_as_string(hide_password=False))
        applications.append(app)
        with TestClient(app) as client:
            raw = b"case,pay,refund\nC002,10000,3000\n"
            material = client.post("/api/v1/customer-materials", headers=headers(), json=payload(raw))
            assert material.status_code == 201, material.text
            material = material.json()
            before = client.get("/api/v1/payments/overview", headers=headers()).json()["summary"]
        with TemporaryFile() as archive:
            subprocess.run(
                ["docker", "exec", container, "pg_dump", "-U", url.username, "-d", source, "-Fc"],
                stdout=archive,
                check=True,
            )
            archive.seek(0)
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    container,
                    "pg_restore",
                    "-U",
                    url.username,
                    "-d",
                    target,
                    "--exit-on-error",
                    "--no-owner",
                ],
                stdin=archive,
                check=True,
            )
        restored = create_app(url.set(database=target).render_as_string(hide_password=False))
        applications.append(restored)
        with TestClient(restored) as client:
            assert client.get(f"/api/v1/customer-materials/{material['id']}/content", headers=headers()).content == raw
            assert client.get("/api/v1/payments/overview", headers=headers()).json()["summary"] == before
            assert (
                client.post("/api/v1/customer-materials", headers=headers(), json=payload(raw)).json()["id"]
                == material["id"]
            )
        # Start the actually deployed v4.15 backend against the restored current schema.
        with TemporaryDirectory() as checkout, TemporaryFile() as source_archive:
            subprocess.run(
                ["git", "archive", "bc18800ec66ed249b4ca665101b09783761163fb", "backend/app"],
                stdout=source_archive,
                cwd=Path(__file__).resolve().parents[2],
                check=True,
            )
            source_archive.seek(0)
            with tarfile.open(fileobj=source_archive) as bundle:
                bundle.extractall(checkout, filter="data")
            rollback_env = os.environ | {
                "DATABASE_URL": url.set(database=target).render_as_string(hide_password=False),
                "ROLLBACK_MATERIAL_ID": material["id"],
                "PYTHONPATH": checkout + "/backend",
                "APP_ENV": "development",
                "AUTH_MODE": "development",
                "ALLOW_DEV_HEADER_AUTH": "true",
                "ALLOW_DEV_TOKEN": "true",
            }
            code = """from app.main import create_app
from app.version import APP_VERSION
from fastapi.testclient import TestClient
import os
assert APP_VERSION == '4.15.0'
with TestClient(create_app()) as client:
    response = client.get('/api/v1/customer-materials/'+os.environ['ROLLBACK_MATERIAL_ID']+'/content',
                          headers={'X-Tenant-ID':'TENANT_A','X-Actor-ID':'Terry'})
    assert response.status_code == 200
    assert response.content == b'case,pay,refund\\nC002,10000,3000\\n'
"""
            subprocess.run([sys.executable, "-c", code], cwd=checkout, env=rollback_env, check=True)
        with restored.state.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM schema_migrations")) == 33
    finally:
        for app in applications:
            app.state.engine.dispose()
        with admin.connect() as connection:
            for name in [source, target]:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
