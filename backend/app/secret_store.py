from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .domain import utcnow
from .models import AuditEvent, ManagedSecret, ServiceConfig

REFERENCE_BACKEND = "reference-only"
ENVELOPE_BACKEND = "local-envelope"
AWS_SECRETS_BACKEND = "aws-secrets-manager"
SUPPORTED_SECRET_BACKENDS = {REFERENCE_BACKEND, ENVELOPE_BACKEND, AWS_SECRETS_BACKEND}


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


def _retire_local_secret(db: Session, reference: str | None, tenant_id: str, service_type: str) -> None:
    if not reference or not reference.startswith("secret://"):
        return
    previous = db.scalar(
        select(ManagedSecret).where(
            ManagedSecret.reference == reference,
            ManagedSecret.tenant_id == tenant_id,
            ManagedSecret.service_type == service_type,
            ManagedSecret.status == "active",
        )
    )
    if previous:
        previous.status = "retired"
        previous.retired_at = utcnow()


def _aws_settings() -> tuple[str, str, str | None, str | None]:
    region = os.getenv("AWS_REGION", "").strip() or os.getenv("AWS_DEFAULT_REGION", "").strip()
    if not region:
        raise SecretStoreError("aws-secrets-manager 需要 AWS_REGION")
    prefix = os.getenv("AWS_SECRET_PREFIX", "luheng/fulfillops").strip().strip("/")
    if not prefix or len(prefix) > 200 or ".." in prefix or not re.fullmatch(r"[A-Za-z0-9/_+=.@-]+", prefix):
        raise SecretStoreError("AWS_SECRET_PREFIX 包含不允许的字符")
    kms_key_id = os.getenv("AWS_KMS_KEY_ID", "").strip() or None
    endpoint_url = os.getenv("AWS_SECRETS_MANAGER_ENDPOINT", "").strip() or None
    return region, prefix, kms_key_id, endpoint_url


def _aws_component(value: str, label: str) -> str:
    if not value or len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9+=.@_-]+", value):
        raise SecretStoreError(f"{label} 不能用于 AWS Secrets Manager 路径")
    return value


def _aws_secret_name(tenant_id: str, service_type: str, suffix: str | None = None) -> str:
    _, prefix, _, _ = _aws_settings()
    tenant = _aws_component(tenant_id, "tenant_id")
    service = _aws_component(service_type, "service_type")
    name = f"{prefix}/{tenant}/{service}/{uuid4().hex if suffix is None else suffix}"
    if len(name) > 512:
        raise SecretStoreError("AWS Secrets Manager 路径超过 512 字符")
    return name


def _aws_name_from_reference(reference: str, tenant_id: str, service_type: str) -> str:
    if not reference.startswith("aws-sm://"):
        raise SecretStoreError("AWS Secrets Manager 引用格式无效")
    name = reference.removeprefix("aws-sm://")
    expected = _aws_secret_name(tenant_id, service_type, "")
    if not name.startswith(expected) or not re.fullmatch(r"[0-9a-f]{32}", name.removeprefix(expected)):
        raise SecretStoreError("AWS Secrets Manager 引用不属于当前租户服务")
    return name


def _aws_payload(tenant_id: str, service_type: str, credential: str) -> str:
    return json.dumps(
        {
            "schema": "luheng-secret-v1",
            "tenant_id": tenant_id,
            "service_type": service_type,
            "credential": credential,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _aws_client() -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise SecretStoreError("aws-secrets-manager 需要安装 requirements-aws.txt") from exc
    region, _, _, endpoint_url = _aws_settings()
    return boto3.client("secretsmanager", region_name=region, endpoint_url=endpoint_url)


def _boto3_available() -> bool:
    return importlib.util.find_spec("boto3") is not None


def _store_aws_secret(
    db: Session,
    tenant_id: str,
    service_type: str,
    credential: str,
    previous_reference: str | None,
) -> str:
    client = _aws_client()
    payload = _aws_payload(tenant_id, service_type, credential)
    try:
        if previous_reference and previous_reference.startswith("aws-sm://"):
            name = _aws_name_from_reference(previous_reference, tenant_id, service_type)
            client.put_secret_value(
                SecretId=name,
                SecretString=payload,
                ClientRequestToken=uuid4().hex,
            )
            return previous_reference
        name = _aws_secret_name(tenant_id, service_type)
        _, _, kms_key_id, _ = _aws_settings()
        request: dict[str, Any] = {
            "Name": name,
            "SecretString": payload,
            "ClientRequestToken": uuid4().hex,
            "Description": "履衡 AI FulfillOps 租户服务凭证",
            "Tags": [
                {"Key": "luheng:tenant", "Value": tenant_id},
                {"Key": "luheng:service", "Value": service_type},
                {"Key": "luheng:managed-by", "Value": "fulfillops"},
            ],
        }
        if kms_key_id:
            request["KmsKeyId"] = kms_key_id
        client.create_secret(**request)
        _retire_local_secret(db, previous_reference, tenant_id, service_type)
        return f"aws-sm://{name}"
    except SecretStoreError:
        raise
    except Exception as exc:
        error_id = hashlib.sha256(type(exc).__name__.encode()).hexdigest()[:8]
        raise SecretStoreError(f"AWS Secrets Manager 写入失败（错误标识 {error_id}）") from exc


def _resolve_aws_secret(reference: str, tenant_id: str, service_type: str) -> str:
    name = _aws_name_from_reference(reference, tenant_id, service_type)
    try:
        response = _aws_client().get_secret_value(SecretId=name)
        raw = response.get("SecretString")
        if not isinstance(raw, str):
            raise SecretStoreError("AWS Secrets Manager 返回的凭证格式不受支持")
        payload = json.loads(raw)
    except SecretStoreError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise SecretStoreError("AWS Secrets Manager 凭证载荷无效") from exc
    except Exception as exc:
        error_id = hashlib.sha256(type(exc).__name__.encode()).hexdigest()[:8]
        raise SecretStoreError(f"AWS Secrets Manager 读取失败（错误标识 {error_id}）") from exc
    if (
        payload.get("schema") != "luheng-secret-v1"
        or payload.get("tenant_id") != tenant_id
        or payload.get("service_type") != service_type
        or not isinstance(payload.get("credential"), str)
        or not payload["credential"]
    ):
        raise SecretStoreError("AWS Secrets Manager 凭证载荷与当前租户服务不匹配")
    return payload["credential"]


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
    if backend == AWS_SECRETS_BACKEND:
        return _store_aws_secret(db, tenant_id, service_type, credential, previous_reference), last4

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
    _retire_local_secret(db, previous_reference, tenant_id, service_type)
    return reference, last4


def resolve_secret(db: Session, reference: str | None, tenant_id: str, service_type: str) -> str | None:
    if not reference:
        return None
    if reference.startswith("aws-sm://"):
        return _resolve_aws_secret(reference, tenant_id, service_type)
    if not reference.startswith("secret://"):
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
    if secret_store_backend() != ENVELOPE_BACKEND:
        return 0
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
    if backend == AWS_SECRETS_BACKEND:
        active_external = db.scalar(
            select(func.count(ServiceConfig.id)).where(
                ServiceConfig.tenant_id == tenant_id,
                ServiceConfig.secret_ref.like("aws-sm://%"),
            )
        ) or 0
        try:
            _, _, kms_key_id, _ = _aws_settings()
            if not _boto3_available():
                raise SecretStoreError("aws-secrets-manager 需要安装 requirements-aws.txt")
        except SecretStoreError as exc:
            return SecretStoreStatus(
                backend=backend,
                status="degraded",
                persistent=True,
                resolvable=False,
                key_version=None,
                active_secrets=active_external,
                rotation_pending=0,
                detail=str(exc),
            )
        return SecretStoreStatus(
            backend=backend,
            status="ready",
            persistent=True,
            resolvable=True,
            key_version="customer-managed" if kms_key_id else "aws-managed",
            active_secrets=active_external,
            rotation_pending=0,
            detail="AWS Secrets Manager 依赖与静态配置已就绪；IAM/KMS 权限在首次读写时验证",
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
