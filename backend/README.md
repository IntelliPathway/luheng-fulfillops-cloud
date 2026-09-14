# 履衡 AI FulfillOps API

`v0.5.0` 后端基线，提供数据库成员权限、JWT/OIDC、带租约的持久异步作业、独立 Worker、统一 Agent Gateway、Provider-neutral Runtime Adapter、DeepSeek Harness SDK/MCP 安全桥、Agent 会话检查点与行动提案，以及多租户接入门禁和活动预检。

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

Compose 默认使用 `external`：API 只入队，`worker` 服务消费 `default` 队列。现有 PostgreSQL 数据卷启动 API 时会按 `migrations/` 自动执行尚未登记的事务迁移。

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

每个运行作业都记录 `lease_owner`、`lease_expires_at` 与 `heartbeat_at`。Worker 定期续租；租约过期时，其他 Worker 会关闭遗留的运行记录，并在 `max_attempts` 预算内重新排队。PostgreSQL 使用 `FOR UPDATE SKIP LOCKED`，SQLite 开发路径使用带状态条件的原子更新。运行中取消为协作式：先写入 `cancel_requested_at`，再由持有租约的 Worker 在当前处理器安全边界收口。

连接测试和五项沙箱自测的新接口以 `/jobs` 结尾并返回 `202`。旧同步接口暂时保留，便于 v0.2 客户端平滑迁移。

## 安全边界

- `credential` 仅用于生成密钥托管引用，明文不会写入业务数据库或响应。
- `settings` 中出现 API Key、Token、Password 等字段会被拒绝。
- 所有读写按 `tenant_id` 过滤。
- 配置变更会撤销旧自测报告及管理员启用状态。
- 活动创建由服务端重新执行保护、重复任务、目标、预算、策略和渠道门禁。

当前密钥托管、Provider 连接与沙箱回执仍为适配器模拟；`secret_ref` 采用 KMS URI 形式，为后续接入 Vault、云 KMS 或企业密钥平台保留契约。生产环境必须关闭开发头认证与开发令牌入口，并配置企业 OIDC issuer、audience 和 JWKS。

DeepSeek Harness 同时支持 `sandbox-contract` 与显式启用的 `python-sdk`。真实模式用 `fulfillops-safe.patch.yml` 禁用官方 `sdk-minimal` 的默认 Shell，仅通过独立 MCP 子进程暴露 8 个受控业务工具；工具回调使用租户与会话绑定的短时 Runtime JWT。安装、环境变量、恢复语义和验收口径见 `harness/README.md`。

真实模式不会从数据库中的 KMS URI 或凭证末四位还原密钥。部署端必须安装 `requirements-harness.txt`、设置 `FULFILLOPS_ENABLE_DSH_RUNTIME=1`，并从 KMS/Secret Manager 注入 `DEEPSEEK_API_KEY` 和独立的 `RUNTIME_JWT_SECRET`。未满足条件时连接作业失败且不会标记 Provider 已连接。

## 测试

```bash
.venv/bin/python -m pytest
```

当前基线为 30 项后端测试，包含 MCP 协议、Runtime JWT、完整异步 Agent 作业、并发 Worker 唯一认领、心跳续租、崩溃恢复、尝试预算、租约 fencing、协作式取消、进程内续接、检查点重放和失败 Run 持久化。
