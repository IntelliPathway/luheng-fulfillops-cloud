# 履衡 AI FulfillOps API

`v0.6.0` 后端基线，提供数据库成员权限、JWT/OIDC、带租约的持久异步作业、独立 Worker、PostgreSQL 通知 Broker、AES-256-GCM 密钥信封、统一 Agent Gateway、DeepSeek Harness SDK/MCP 安全桥、Agent 会话检查点与行动提案，以及多租户接入门禁和活动预检。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DATABASE_URL=sqlite:///./luheng-dev.db .venv/bin/uvicorn app.main:app --reload --port 8000
```

接口文档：`http://127.0.0.1:8000/api/docs`。

前端开发服务器通过 `/api` 代理到 `127.0.0.1:8000`。也可以从项目根目录运行 `docker compose up --build`，同时启动 PostgreSQL、API 和前端。

默认 `JOB_EXECUTION_MODE=inline`，适合本地零依赖开发。独立进程验证可分别启动：

```bash
JOB_EXECUTION_MODE=external DATABASE_URL=sqlite:///./luheng-dev.db .venv/bin/uvicorn app.main:app --port 8000
JOB_EXECUTION_MODE=external DATABASE_URL=sqlite:///./luheng-dev.db .venv/bin/python -m app.worker
```

Compose 默认使用 `external`：API 只入队，`worker` 服务消费 `default` 队列。`postgres-notify` 通过事务提交后的通知唤醒 Worker，数据库轮询仍负责恢复丢失通知。现有 PostgreSQL 数据卷启动 API 时会按 `migrations/` 自动执行尚未登记的事务迁移。

## 请求上下文

除健康检查与受控的本地开发令牌入口外，接口必须携带：

- `X-Tenant-ID`：租户标识。
- `Authorization: Bearer <token>`：生产推荐，由 OIDC/JWT 提供用户 subject。
- `X-Actor-ID`：仅在 `ALLOW_DEV_HEADER_AUTH=true` 的本地开发环境使用。

角色不再从请求头读取，而由 `tenant_memberships` 按用户与租户解析。配置、连接测试、自测和启用仅允许管理员；活动预检、创建和 Agent 行动确认允许运营人员和管理员；只读 Agent/ChatBI 查询允许观察员。

## Agent 与持久作业

- `GET /api/v1/agents/gateway`：查看当前 Hermes/DeepSeek Harness/LangGraph/自研 Runtime、传输方式、允许与禁止工具及审批边界。
- `POST /api/v1/agents/sessions`：按全局、活动或案件范围创建持久会话。
- `POST /api/v1/agents/sessions/{id}/messages`：把对话作为异步作业提交，并持久化回答、来源、工具轨迹和证据。
- `GET /api/v1/agents/sessions/{id}/runtime`：读取 Provider Session、事件游标、Turn 数和最近 Run，用于重启后恢复与运维核查。
- `POST /api/v1/agents/proposals/{id}/confirm`：对暂停/恢复提案做结构化确认，服务端再次校验角色、租户和状态。
- `GET /api/v1/jobs/{id}`：恢复连接测试、自测或 Agent 运行进度；失败作业可重试，排队/运行作业可取消。
- `GET /api/v1/jobs/queue/health`：读取当前租户排队/运行/陈旧任务，以及全局可用 Worker 数。
- `GET /api/v1/security/secrets/health`：管理员查看密钥后端、可解密状态、活动密钥数和当前密钥版本；不返回引用或密文。

每个运行作业都记录 `lease_owner`、`lease_expires_at` 与 `heartbeat_at`。Worker 定期续租；租约过期时，其他 Worker 会关闭遗留的运行记录，并在 `max_attempts` 预算内重新排队。PostgreSQL 使用 `FOR UPDATE SKIP LOCKED`，SQLite 开发路径使用带状态条件的原子更新。运行中取消为协作式：先写入 `cancel_requested_at`，再由持有租约的 Worker 在当前处理器安全边界收口。

连接测试和五项沙箱自测的新接口以 `/jobs` 结尾并返回 `202`。旧同步接口暂时保留，便于 v0.2 客户端平滑迁移。

## 密钥信封与安全边界

- `SECRET_STORE_BACKEND=reference-only` 是默认零密钥路径，只生成外部 KMS 引用，不能从数据库恢复明文。
- `SECRET_STORE_BACKEND=local-envelope` 使用 `SECRET_MASTER_KEY` 的 32 字节 URL-safe Base64 密钥执行 AES-256-GCM；数据库只保存密文、随机 nonce、算法和密钥版本。
- 密文的 AAD 同时绑定引用、租户与服务类型，跨租户或跨服务读取会失败；更新凭证会让旧信封进入 `retired`。
- `SECRET_PREVIOUS_KEYS` 可在主密钥轮换窗口内按版本解密旧密文，完成重写后再从部署配置移除旧密钥。
- 设置新主密钥与旧密钥表后运行 `python -m app.secret_cli rotate`，可把全部活动信封原地重包裹并写入无敏感内容的审计事件；也可追加 `--tenant TENANT_A` 限定租户。`status --tenant TENANT_A` 会报告待轮换数量。
- 解密后的模型凭证只短暂进入 Runtime 配置内存，不写回服务 settings、作业 payload、日志、审计事件或 API 响应。
- `settings` 中出现 API Key、Token、Password 等字段会被拒绝。
- 所有读写按 `tenant_id` 过滤。
- 配置变更会撤销旧自测报告及管理员启用状态。
- 活动创建由服务端重新执行保护、重复任务、目标、预算、策略和渠道门禁。

`local-envelope` 是可运行的自托管信封实现，不等同于云 KMS 或 HSM。生产环境应由 KMS 注入主密钥，后续也可按相同契约增加 Vault、AWS Secrets Manager、云 KMS 或企业密钥平台 Provider。生产环境必须关闭开发头认证与开发令牌入口，并配置企业 OIDC issuer、audience 和 JWKS。

DeepSeek Harness 同时支持 `sandbox-contract` 与显式启用的 `python-sdk`。真实模式用 `fulfillops-safe.patch.yml` 禁用官方 `sdk-minimal` 的默认 Shell，仅通过独立 MCP 子进程暴露 8 个受控业务工具；工具回调使用租户与会话绑定的短时 Runtime JWT。安装、环境变量、恢复语义和验收口径见 `harness/README.md`。

真实模式可以使用 `local-envelope` 中当前租户的模型密钥，也可继续从部署环境读取 `DEEPSEEK_API_KEY`。部署端仍必须安装 `requirements-harness.txt`、设置 `FULFILLOPS_ENABLE_DSH_RUNTIME=1` 并注入独立的 `RUNTIME_JWT_SECRET`；未满足条件时连接作业失败且不会标记 Provider 已连接。

## 测试

```bash
.venv/bin/python -m pytest
```

当前本地基线为 38 项通过、1 项 PostgreSQL 专项按环境跳过；GitHub Actions 注入 PostgreSQL 后执行完整 39 项。覆盖 MCP、Runtime JWT、并发 Worker、崩溃恢复、租约 fencing、协作取消、密钥信封、轮换、Runtime 注入，以及从 v0.4 表结构升级并通过通知 Broker 完成任务。
