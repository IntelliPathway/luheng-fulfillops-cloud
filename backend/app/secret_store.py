from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .domain import utcnow
from .models import AuditEvent, ManagedSecret

REFERENCE_BACKEND = "reference-only"
ENVELOPE_BACKEND = "local-envelope"
SUPPORTED_SECRET_BACKENDS = {REFERENCE_BACKEND, ENVELOPE_BACKEND}


class SecretStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecretStoreStatus:
    backend: str
    status: str
    persistent: bool
    resolvable: bool
    key_version: str | None
    active_secrets: int
    rotation_pending: int
    detail: str


def secret_store_backend() -> str:
    backend = os.getenv("SECRET_STORE_BACKEND", REFERENCE_BACKEND).strip().lower()
    if backend not in SUPPORTED_SECRET_BACKENDS:
        raise SecretStoreError(f"SECRET_STORE_BACKEND 不支持：{backend}")
    return backend


def _decode_key(encoded: str, variable: str) -> bytes:
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        key = base64.urlsafe_b64decode(padded.encode())
    except Exception as exc:
        raise SecretStoreError(f"{variable} 必须是 URL-safe Base64") from exc
    if len(key) != 32:
        raise SecretStoreError(f"{variable} 解码后必须为 32 字节")
    return key


def _keyring() -> tuple[str, dict[str, bytes]]:
    version = os.getenv("SECRET_MASTER_KEY_VERSION", "local-v1").strip()
    current = os.getenv("SECRET_MASTER_KEY", "").strip()
    if not current:
        raise SecretStoreError("local-envelope 需要 SECRET_MASTER_KEY")
    keys = {version: _decode_key(current, "SECRET_MASTER_KEY")}
    previous_raw = os.getenv("SECRET_PREVIOUS_KEYS", "").strip()
    if previous_raw:
        try:
            previous = json.loads(previous_raw)
        except json.JSONDecodeError as exc:
            raise SecretStoreError("SECRET_PREVIOUS_KEYS 必须是 JSON 对象") from exc
        if not isinstance(previous, dict):
            raise SecretStoreError("SECRET_PREVIOUS_KEYS 必须是 JSON 对象")
        for key_version, encoded in previous.items():
            keys[str(key_version)] = _decode_key(str(encoded), f"SECRET_PREVIOUS_KEYS[{key_version}]")
    return version, keys


def _aad(reference: str, tenant_id: str, service_type: str) -> bytes:
    return f"{reference}\n{tenant_id}\n{service_type}".encode()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode())


def store_secret(
    db: Session,
    tenant_id: str,
    service_type: str,
    credential: str,
    *,
    previous_reference: str | None = None,
) -> tuple[str, str]:
    backend = secret_store_backend()
    last4 = credential[-4:]
    if backend == REFERENCE_BACKEND:
        return f"kms://luheng/{tenant_id}/{service_type}/{uuid4().hex}", last4

    key_version, keys = _keyring()
    reference = f"secret://luheng/{tenant_id}/{service_type}/{uuid4().hex}"
    nonce = os.urandom(12)
    ciphertext = AESGCM(keys[key_version]).encrypt(
        nonce,
        credential.encode(),
        _aad(reference, tenant_id, service_type),
    )
    db.add(
        ManagedSecret(
            reference=reference,
            tenant_id=tenant_id,
            service_type=service_type,
            ciphertext=_encode(ciphertext),
            nonce=_encode(nonce),
            key_version=key_version,
        )
    )
    if previous_reference and previous_reference.startswith("secret://"):
        previous = db.scalar(
            select(ManagedSecret).where(
                ManagedSecret.reference == previous_reference,
                ManagedSecret.tenant_id == tenant_id,
                ManagedSecret.service_type == service_type,
                ManagedSecret.status == "active",
            )
        )
        if previous:
            previous.status = "retired"
            previous.retired_at = utcnow()
    return reference, last4


def resolve_secret(db: Session, reference: str | None, tenant_id: str, service_type: str) -> str | None:
    if not reference or not reference.startswith("secret://"):
        return None
    secret = db.scalar(
        select(ManagedSecret).where(
            ManagedSecret.reference == reference,
            ManagedSecret.tenant_id == tenant_id,
            ManagedSecret.service_type == service_type,
            ManagedSecret.status == "active",
        )
    )
    if not secret:
        raise SecretStoreError("密钥引用不存在、已退役或不属于当前租户服务")
    _, keys = _keyring()
    key = keys.get(secret.key_version)
    if not key:
        raise SecretStoreError(f"缺少密钥版本 {secret.key_version}")
    try:
        plaintext = AESGCM(key).decrypt(
            _decode(secret.nonce),
            _decode(secret.ciphertext),
            _aad(secret.reference, tenant_id, service_type),
        )
        return plaintext.decode()
    except Exception as exc:
        raise SecretStoreError("密钥密文校验失败") from exc


def rewrap_active_secrets(db: Session, tenant_id: str | None = None) -> int:
    current_version, keys = _keyring()
    statement = select(ManagedSecret).where(
        ManagedSecret.status == "active",
        ManagedSecret.key_version != current_version,
    )
    if tenant_id:
        statement = statement.where(ManagedSecret.tenant_id == tenant_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    rotated = 0
    for secret in db.scalars(statement):
        previous_version = secret.key_version
        old_key = keys.get(secret.key_version)
        if not old_key:
            raise SecretStoreError(f"缺少密钥版本 {secret.key_version}，无法重包裹 {secret.id}")
        aad = _aad(secret.reference, secret.tenant_id, secret.service_type)
        try:
            plaintext = AESGCM(old_key).decrypt(_decode(secret.nonce), _decode(secret.ciphertext), aad)
        except Exception as exc:
            raise SecretStoreError(f"密钥 {secret.id} 密文校验失败") from exc
        nonce = os.urandom(12)
        secret.ciphertext = _encode(AESGCM(keys[current_version]).encrypt(nonce, plaintext, aad))
        secret.nonce = _encode(nonce)
        secret.key_version = current_version
        db.add(
            AuditEvent(
                tenant_id=secret.tenant_id,
                actor_id="system:secret-rotation",
                action="secret.rewrapped",
                resource_type="managed_secret",
                resource_id=secret.id,
                detail={
                    "service_type": secret.service_type,
                    "previous_key_version": previous_version,
                    "key_version": current_version,
                },
            )
        )
        rotated += 1
    return rotated


def secret_store_status(db: Session, tenant_id: str) -> SecretStoreStatus:
    backend = secret_store_backend()
    active = db.scalar(
        select(func.count(ManagedSecret.id)).where(
            ManagedSecret.tenant_id == tenant_id,
            ManagedSecret.status == "active",
        )
    ) or 0
    if backend == REFERENCE_BACKEND:
        return SecretStoreStatus(
            backend=backend,
            status="reference-only",
            persistent=False,
            resolvable=False,
            key_version=None,
            active_secrets=active,
            rotation_pending=0,
            detail="只保存外部 KMS 引用；运行凭证需由部署环境注入",
        )
    try:
        key_version, _ = _keyring()
    except SecretStoreError as exc:
        return SecretStoreStatus(
            backend=backend,
            status="degraded",
            persistent=True,
            resolvable=False,
            key_version=None,
            active_secrets=active,
            rotation_pending=active,
            detail=str(exc),
        )
    rotation_pending = db.scalar(
        select(func.count(ManagedSecret.id)).where(
            ManagedSecret.tenant_id == tenant_id,
            ManagedSecret.status == "active",
            ManagedSecret.key_version != key_version,
        )
    ) or 0
    return SecretStoreStatus(
        backend=backend,
        status="ready",
        persistent=True,
        resolvable=True,
        key_version=key_version,
        active_secrets=active,
        rotation_pending=rotation_pending,
        detail="AES-256-GCM 信封存储已就绪；主密钥不进入数据库",
    )
