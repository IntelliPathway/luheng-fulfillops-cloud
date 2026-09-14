# 履衡 AI FulfillOps API

`v0.3.1` 后端基线，提供数据库成员权限、JWT/OIDC、持久异步作业、统一 Agent Gateway、Provider-neutral Runtime Adapter、Agent 会话检查点与行动提案，以及多租户接入门禁和活动预检。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DATABASE_URL=sqlite:///./luheng-dev.db .venv/bin/uvicorn app.main:app --reload --port 8000
```

接口文档：`http://127.0.0.1:8000/api/docs`。

前端开发服务器通过 `/api` 代理到 `127.0.0.1:8000`。也可以从项目根目录运行 `docker compose up --build`，同时启动 PostgreSQL、API 和前端。

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

连接测试和五项沙箱自测的新接口以 `/jobs` 结尾并返回 `202`。旧同步接口暂时保留，便于 v0.2 客户端平滑迁移。

## 安全边界

- `credential` 仅用于生成密钥托管引用，明文不会写入业务数据库或响应。
- `settings` 中出现 API Key、Token、Password 等字段会被拒绝。
- 所有读写按 `tenant_id` 过滤。
- 配置变更会撤销旧自测报告及管理员启用状态。
- 活动创建由服务端重新执行保护、重复任务、目标、预算、策略和渠道门禁。

当前密钥托管、Provider 连接与沙箱回执仍为适配器模拟；`secret_ref` 采用 KMS URI 形式，为后续接入 Vault、云 KMS 或企业密钥平台保留契约。生产环境必须关闭开发头认证与开发令牌入口，并配置企业 OIDC issuer、audience 和 JWKS。

DeepSeek Harness 当前仅实现 `sandbox-contract`。它验证 8 个受控业务工具、7 类危险工具阻断、会话检查点与恢复语义，但不会启动外部 SDK。官方 Python SDK/JSON-RPC 接入必须先实现并验收 `backend/harness/fulfillops-safe.contract.json` 对应的专用插件，禁止使用含 shell/文件系统能力的默认 Profile 直接接触业务数据。

## 测试

```bash
.venv/bin/python -m pytest
```
