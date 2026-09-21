# 履约智控 AI · RepayGuard AI 业务 API

`v1.9.0` 后端基线新增联系取消、受控重试、验签电话事件时间线与人工接管，并继续提供服务端权威活动、审计查询、AI 指挥台和不可变财务账簿。生产启动配置、迁移、演示种子和首租户初始化已经分离；Sites 只承载前端交互沙箱。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
APP_ENV=development SEED_DEMO_DATA=true DATABASE_URL=sqlite:///./luheng-dev.db \
  .venv/bin/uvicorn app.main:app --reload --port 8000
```

接口文档：`http://127.0.0.1:8000/api/docs`。`/api/v1/health/live` 检查进程，`/api/v1/health/ready` 会查询数据库。

生产必须设置 `APP_ENV=production`，关闭 `AUTO_CREATE_SCHEMA`、`SEED_DEMO_DATA` 和两个开发认证开关。完整变量见 `docs/configuration.md`。

前端开发服务器通过 `/api` 代理到 `127.0.0.1:8000`。也可以从项目根目录运行 `docker compose up --build`，同时启动 PostgreSQL、API 和前端。

默认 `JOB_EXECUTION_MODE=inline`，适合本地零依赖开发。独立进程验证可分别启动：

```bash
JOB_EXECUTION_MODE=external DATABASE_URL=sqlite:///./luheng-dev.db .venv/bin/uvicorn app.main:app --port 8000
JOB_EXECUTION_MODE=external DATABASE_URL=sqlite:///./luheng-dev.db .venv/bin/python -m app.worker
```

Compose 默认使用 `external`：API 只入队，`worker` 服务消费 `default` 队列。`postgres-notify` 通过事务提交后的通知唤醒 Worker，数据库轮询仍负责恢复丢失通知。现有 PostgreSQL 数据卷启动 API 时会按 `migrations/` 自动执行尚未登记的事务迁移。生产不调用 SQLAlchemy `create_all`；SQLite 建表和兼容升级只属于开发/测试路径。

## 请求上下文

除健康检查与受控的本地开发令牌入口外，接口必须携带：

- `X-Tenant-ID`：租户标识。
- `Authorization: Bearer <token>`：生产推荐，由 OIDC/JWT 提供用户 subject。
- `X-Actor-ID`：仅在 `ALLOW_DEV_HEADER_AUTH=true` 的本地开发环境使用。

角色不再从请求头读取，而由 `tenant_memberships` 按用户与租户解析。配置、连接测试、自测和启用仅允许管理员；活动预检、创建和 Agent 行动确认允许运营人员和管理员；只读 Agent/ChatBI 查询允许观察员。

## 资产与案件导入

- `POST /api/v1/asset-imports/previews`：接收 UTF-8 CSV，生成租户级持久预演批次。
- `GET /api/v1/asset-imports`：读取当前工作空间最近批次及问题摘要。
- `POST /api/v1/asset-imports/{id}/commit`：由不同账号的管理员确认并原子落库。

导入契约 `asset-case-v2` 接受资产包、案件、整数分债权余额、可选债务拆分、委托期限、逾期/联系时间、佣金规则、联系依据引用和安全初始状态。姓名、手机号、证件号、地址、邮箱及未声明字段会阻断整批预演。数据库只保存原文件 SHA-256、标准化非敏感行和问题报告，不保存原始 CSV。已有案件被跳过且不覆盖；提交时若预演已陈旧，则整批失败关闭。新资产包默认使用草稿策略，不能直接进入生产触达。

## 服务端权威资产目录

| 接口 | 作用 |
|---|---|
| `GET /api/v1/asset-packages` | 租户级资产包搜索、策略筛选、分页和业务聚合 |
| `GET /api/v1/cases` | 案件搜索、资产包/状态视图、排序、分页和分面计数 |
| `GET /api/v1/cases/{case_id}` | 单案债权、委托、保护、计划、回款和佣金摘要 |

目录只返回非敏感业务字段，并用 `data_source=server-authoritative` 标明来源。数据完整度由财务档案、余额、委托区间、有效佣金规则和联系依据引用确定；账龄、最近联系等未入库事实不会由前端编造。专用列表执行服务端分页，API 在线但目录读取失败时前端失败关闭。

## Agent 与持久作业

- `GET /api/v1/agents/gateway`：查看当前 Hermes/DeepSeek Harness/LangGraph/自研 Runtime、传输方式、允许与禁止工具及审批边界。
- `POST /api/v1/agents/sessions`：按全局、活动或案件范围创建持久会话。
- `POST /api/v1/agents/sessions/{id}/messages`：把对话作为异步作业提交，并持久化回答、来源、工具轨迹和证据。
- `GET /api/v1/agents/sessions/{id}/runtime`：读取 Provider Session、事件游标、Turn 数和最近 Run，用于重启后恢复与运维核查。
- `POST /api/v1/agents/proposals/{id}/confirm`：对暂停/恢复提案做结构化确认，服务端再次校验角色、租户和状态。
- `GET /api/v1/jobs/{id}`：恢复连接测试、自测或 Agent 运行进度；失败作业可重试，排队/运行作业可取消。
- `GET /api/v1/jobs/queue/health`：读取当前租户排队/运行/陈旧任务，以及全局可用 Worker 数。
- `GET /api/v1/security/secrets/health`：管理员查看密钥后端、可解密状态、活动密钥数和当前密钥版本；不返回引用或密文。
- `GET /api/v1/security/auth/health`：管理员查看 OIDC 校验模式、算法/声明策略与开发认证开关；不返回 IdP 地址。
- `GET /api/v1/models/gateway/health`：管理员查看模型执行模式、允许端点主机、Token/费用上限和真实调用门禁；不返回密钥或完整请求。
- `POST /api/v1/agents/replays/jobs`：把 `fulfillops-safe-core` 安全回放作为持久作业提交。
- `GET /api/v1/agents/replays`：读取当前租户的回放结果、数据集摘要、逐项检查和输出摘要。

每个运行作业都记录 `lease_owner`、`lease_expires_at` 与 `heartbeat_at`。Worker 定期续租；租约过期时，其他 Worker 会关闭遗留的运行记录，并在 `max_attempts` 预算内重新排队。PostgreSQL 使用 `FOR UPDATE SKIP LOCKED`，SQLite 开发路径使用带状态条件的原子更新。运行中取消为协作式：先写入 `cancel_requested_at`，再由持有租约的 Worker 在当前处理器安全边界收口。

连接测试和五项沙箱自测的新接口以 `/jobs` 结尾并返回 `202`。旧同步接口暂时保留，便于 v0.2 客户端平滑迁移。

## 密钥信封与安全边界

- `SECRET_STORE_BACKEND=reference-only` 是默认零密钥路径，只生成外部 KMS 引用，不能从数据库恢复明文。
- `SECRET_STORE_BACKEND=local-envelope` 使用 `SECRET_MASTER_KEY` 的 32 字节 URL-safe Base64 密钥执行 AES-256-GCM；数据库只保存密文、随机 nonce、算法和密钥版本。
- `SECRET_STORE_BACKEND=aws-secrets-manager` 使用工作负载角色调用 AWS；安装 `requirements-aws.txt` 并配置 `AWS_REGION`。应用数据库只保存 `aws-sm://` 引用和末四位掩码，云端 JSON 载荷再次绑定租户与服务。
- `AWS_SECRET_PREFIX` 默认为 `luheng/fulfillops`；可选 `AWS_KMS_KEY_ID` 使用客户托管 KMS Key。应用不读取或持久化静态 AWS Access Key，推荐实例、ECS/EKS 或工作负载身份。
- 密文的 AAD 同时绑定引用、租户与服务类型，跨租户或跨服务读取会失败；更新凭证会让旧信封进入 `retired`。
- `SECRET_PREVIOUS_KEYS` 可在主密钥轮换窗口内按版本解密旧密文，完成重写后再从部署配置移除旧密钥。
- 设置新主密钥与旧密钥表后运行 `python -m app.secret_cli rotate`，可把全部活动信封原地重包裹并写入无敏感内容的审计事件；也可追加 `--tenant TENANT_A` 限定租户。`status --tenant TENANT_A` 会报告待轮换数量。
- 解密后的模型凭证只短暂进入 Runtime 配置内存，不写回服务 settings、作业 payload、日志、审计事件或 API 响应。
- `settings` 中出现 API Key、Token、Password 等字段会被拒绝。
- 所有读写按 `tenant_id` 过滤。
- 配置变更会撤销旧自测报告及管理员启用状态。
- 活动创建由服务端重新执行保护、重复任务、目标、预算、策略和渠道门禁。

`local-envelope` 是可运行的自托管信封实现，不等同于云 KMS 或 HSM。AWS 后端的健康接口只证明依赖和配置完整，实际 IAM/KMS 权限在首次读写时验证。生产环境必须关闭开发头认证与开发令牌入口，并配置企业 OIDC issuer、audience 和 JWKS。

## 企业 OIDC

设置 `AUTH_MODE=oidc` 后，Bearer 身份只接受 JWKS 公钥验证。JWKS 默认必须使用无内嵌凭证的 HTTPS 地址；仅封闭测试网络可显式设置 `OIDC_ALLOW_INSECURE_JWKS=true`。默认仅允许 `RS256`，可通过 `OIDC_ALLOWED_ALGORITHMS` 显式增加受支持的 RSA、PSS、ECDSA 或 EdDSA 算法；对称 `HS*` 与 `none` 永远不能用于该模式。默认要求 `sub,exp,iat`，`OIDC_REQUIRED_CLAIMS` 只能追加不能移除这些声明。

`OIDC_LEEWAY_SECONDS` 限制在 0—300 秒，`OIDC_JWKS_CACHE_SECONDS` 限制在 60—86400 秒。若企业 IdP 提供租户授权声明，可设置 `OIDC_TENANT_CLAIM`；字符串或数组中必须包含当前 `X-Tenant-ID`。该声明只缩小租户范围，用户角色仍从 `tenant_memberships` 读取。

## Agent 安全回放

`fulfillops-safe-core` v1 包含钱指标查询、保护案件、暂停提案和 Shell 越权阻断四个回放。`deterministic-contract` 通过与普通 Agent 相同的安全适配器和工具策略运行，但使用隔离会话、不解析模型凭证、不执行行动提案。

`live-provider` 会先完成上述确定性检查，再把不含业务原文的断言/工具状态摘要交给模型做一次 JSON 安全评估。它要求 `ENABLE_LIVE_MODEL_CALLS=true`、模型配置选择 `live-provider`、Endpoint/模型命中部署允许列表、密钥可解析、当前版本连接测试通过和管理员明确确认。每次运行最多一次外部调用且不自动重试，以避免重复计费。`model_replay_runs` 只保存不可逆摘要、Token、保守费用、配置版本和 Provider 请求 ID 哈希。

## 受控模型 Gateway

默认 `executionMode=contract-only`，连接测试只验证策略而不访问 Provider。真实模式使用 OpenAI-compatible `/chat/completions` 和 JSON Object 输出；不跟随重定向，默认只允许 `api.deepseek.com`，并拒绝 URL 凭证、查询参数、本地/私有地址和非标准端口。企业网关必须通过 `MODEL_EGRESS_ALLOWLIST` 显式加入；私网与 HTTP 开关只供封闭测试环境。

单次请求限制 12,000 字符、5—60 秒超时、64—4,096 输出 Token 和租户配置/部署配置的双重费用上限。预算使用 `MODEL_COST_CEILING_USD_PER_M_TOKENS` 做保守预留和事后核对，并非账单结算口径。Provider 错误只保留标准化错误码，不保存响应正文。

DeepSeek Harness 同时支持 `sandbox-contract` 与显式启用的 `python-sdk`。真实模式用 `fulfillops-safe.patch.yml` 禁用官方 `sdk-minimal` 的默认 Shell，仅通过独立 MCP 子进程暴露 8 个受控业务工具；工具回调使用租户与会话绑定的短时 Runtime JWT。安装、环境变量、恢复语义和验收口径见 `harness/README.md`。

真实模式可以使用 `local-envelope` 中当前租户的模型密钥，也可继续从部署环境读取 `DEEPSEEK_API_KEY`。部署端仍必须安装 `requirements-harness.txt`、设置 `FULFILLOPS_ENABLE_DSH_RUNTIME=1` 并注入独立的 `RUNTIME_JWT_SECRET`；未满足条件时连接作业失败且不会标记 Provider 已连接。

## 支付回执与财务账簿

- `PUT /api/v1/payments/webhook-configs/{provider}`：管理员配置 Provider 签名密钥和单笔金额上限；密钥进入现有密钥后端，API 仅返回末四位。
- `POST /api/v1/webhooks/payments/{tenant}/{provider}`：对精确原始请求体校验 `X-FulfillOps-Timestamp` 和 `X-FulfillOps-Signature: v1=<HMAC-SHA256>`。未配置、坏签名、过期签名和金额超限均失败关闭。
- `GET /api/v1/payments/overview`：按当前成员租户读取整数分汇总、回款账簿、佣金账簿、待复核回执和对账记录。
- `GET /api/v1/payments/receipts/{id}/candidates`：读取当前租户内且财务档案完整的确定性候选建议，不执行匹配。
- `POST /api/v1/payments/receipts/{id}/reconciliations`：运营人员或管理员创建带候选快照、证据摘要和版本号的匹配提案。
- `POST /api/v1/payments/reconciliations/{id}/decision`：由不同账号的管理员独立批准或驳回；批准后才原子入账。
- `POST /api/v1/payments/receipts/{id}/match`：旧版单步接口默认停用；仅兼容部署显式设置 `ALLOW_LEGACY_PAYMENT_MATCH=true` 且管理员调用时可用。
- `POST /api/v1/commissions/events`：管理员明确确认结算或实收；结算不能超过应计余额，实收不能超过已结算未收余额。
- `POST /api/v1/payments/sandbox-receipts`：仅在 `ENABLE_PAYMENT_SANDBOX=true` 时提供，生成 HMAC 回执后复用正式入账路径。

回执按 `(tenant_id, provider, provider_event_id)` 唯一；完全相同的原始载荷返回原记录并增加重复计数，不同载荷复用事件号返回 HTTP 409。无法自动匹配的已验签回执进入复核队列且不更新钱指标。退款必须引用已匹配原支付，累计金额不能超过原支付，并沿用原事件的计佣资格和比例。Agent 工具仍禁止 `payment.write` 和 `commission.write`，只能查询账簿或生成人工确认提案。

正式环境应配置 `PAYMENT_WEBHOOK_TOLERANCE_SECONDS=300`（允许范围 60—900），保持 `ENABLE_PAYMENT_SANDBOX=false`，并通过 KMS/Secrets Manager 提供各租户 Provider 密钥。系统不保存原始支付请求体或签名，只保存 SHA-256 摘要、验签版本和不可变业务事件。

## 签约方案与分期履约

- `GET /api/v1/repayment-plans/overview`：读取当前租户的待复核、生效、完成、逾期及临近到期方案。
- `GET /api/v1/cases/{case_id}/repayment-plans`：读取案件的版本化签约与期次事实。
- `POST /api/v1/cases/{case_id}/repayment-plans`：运营人员或管理员提交方案、外部协议引用和 SHA-256 摘要，不保存协议正文。
- `POST /api/v1/repayment-plans/{id}/decision`：由不同账号的管理员批准或驳回；批准时锁定案件、档案和资产包并重新校验当前策略。

方案金额使用整数分。服务端强制校验债权余额、最低结算比例、最低首付比例、最大期数、连续期次、期次合计、签署时间和有效委托期；保护案件、陈旧版本、自审及同案重复生效方案均失败关闭。已验签回款只会分摊到签署时间不晚于回款的生效方案，并按最早到期顺序入账；退款引用原回款并从其最近期次分摊逆向冲销，方案全额履行或退款回退时同步调整案件状态。

## 测试

```bash
.venv/bin/python -m pytest
```

生产保障冒烟可从仓库根目录运行 `./scripts/smoke-v08-model-gateway.sh`、`./scripts/smoke-v09-financial-ledger.sh`、`./scripts/smoke-v10-reconciliation.sh`、`./scripts/smoke-v11-protection.sh`、`./scripts/smoke-v12-repayment-plans.sh`、`./scripts/smoke-v15-asset-import.sh` 和 `./scripts/smoke-v16-asset-catalog.sh`。v0.16 冒烟覆盖服务端分页、保护视图、钱指标聚合、跨租户 404，以及新导入案件立即进入权威目录。

当前共收集 128 项后端测试：本地 127 项通过、1 项 PostgreSQL 专项按环境跳过；CI 注入 PostgreSQL 17 后执行完整测试。覆盖启动失败关闭、显式种子、首租户初始化、CSV 导入与隐私字段阻断、权威目录分页/筛选/统计、活动委托期与基础资格门禁、MCP、Runtime JWT、并发 Worker、崩溃恢复、租约 fencing、协作取消、密钥 Provider、OIDC/JWKS、模型出网/费用门禁、真实回放零原文持久化、支付验签/幂等/匹配、对账提案双人分权、保护事件、签约方案与分期分摊、跨租户隔离、退款与佣金账簿、SQLite 兼容升级，以及从 v0.4 表结构升级并通过通知 Broker 完成任务。
