# DeepSeek Harness 接入边界

v0.4 同时提供零外部调用的 `sandbox-contract` 和可显式启用的 `python-sdk`。两种模式共享
`fulfillops-safe.contract.json`，但连接状态与运行元数据不会混用。

从 v0.5 起，Compose 中的真实 Runtime 由独立 Worker 启动。Worker 回调 API 时使用服务名地址 `http://api:8000`；本机内联模式仍可使用 `http://127.0.0.1:8000`。

从 v0.6 起，模型凭证可由 `local-envelope` 在数据库中加密持久化，并在启动 Harness 前按租户和服务瞬时解密；`DEEPSEEK_API_KEY` 环境注入仍作为部署覆盖路径。凭证不会进入检查点、事件日志或 MCP 子进程环境，SDK 仅通过构造参数接收。

## 安全组成

- `fulfillops-safe.patch.yml` 覆盖官方 `sdk-minimal` Profile，首先禁用默认的 `persistent-bash` 和 `persistent-pwsh`。
- Patch 只插入一个 `@deepseek-ai/dsh-mcp-client`，由它启动 `python -m app.harness_mcp`。
- MCP Server 仅发布 8 个查询/提案工具；模型看到的名称为 `mcp__fulfillops__*`。
- MCP 工具通过固定的 `FULFILLOPS_INTERNAL_URL` 回调 API。每个进程获得一个 20 分钟短时 JWT，令牌绑定租户、Agent Session 和会话范围。
- 内部 API 从令牌解析租户，拒绝工具参数覆盖租户，并对案件/活动资源再次执行 scope 门禁；提案工具不执行活动修改，最终确认仍经过用户 RBAC、状态和有效期复核。
- 模型凭证不从 `credential_mask` 或业务数据库还原，只能由 KMS/运行环境注入 `DEEPSEEK_API_KEY`。

## 两种运行模式

- `sandbox-contract`：验证租户隔离、工具白名单、行动提案和数据库检查点；不启动外部进程，不调用真实模型。
- `python-sdk`：启动官方 `deepseek-harness-sdk` 的 JSON-RPC/stdio 子进程。必须安装 `requirements-harness.txt`、设置 `FULFILLOPS_ENABLE_DSH_RUNTIME=1` 并注入模型凭证；缺一项时连接测试明确失败。

进程存活时，同一 Agent Session 复用相同 Provider Session。进程重启后，履衡数据库会把最近的脱敏消息作为检查点重放到一个新的 Provider Session，并要求涉及业务事实时重新调用工具。原因是当前官方 SDK 协议没有 resume/open 方法，复用已落盘的相同 Session ID 可能冲突。

## 安装与启用

```bash
python -m pip install -r requirements.txt -r requirements-harness.txt
export FULFILLOPS_ENABLE_DSH_RUNTIME=1
export DEEPSEEK_API_KEY='由 KMS 或部署平台注入'
export RUNTIME_JWT_SECRET='独立随机密钥'
export FULFILLOPS_INTERNAL_URL='http://127.0.0.1:8000'
```

容器构建时使用 `INSTALL_HARNESS=true docker compose build api`。生产环境应把 `FULFILLOPS_DSH_ROOT` 指向受限持久卷，并固定 SDK 版本后执行协议回归。

## 验收口径

至少覆盖 C002 案件查询、钱指标口径、ACT-001 暂停提案、进程内续接、进程重启检查点重放、跨租户拒绝、令牌过期和全部禁用工具拒绝。官方 SDK 的真实模型回放属于部署验收；仓库测试使用协议级假 Runtime，不消耗模型额度。
