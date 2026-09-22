# 配置参考

本文列出 `v2.3.0` 运行时和构建时配置。示例值只用于说明；真实密钥不得写入 Git、Compose 文本、前端变量或聊天记录。

## 1. 配置加载规则

- 应用直接读取进程环境变量，不会自动加载 `.env` 文件。
- Docker Compose 会从指定的 `--env-file` 做变量替换，再把配置传入容器。
- `backend/.env.example` 是本地开发示例。
- `deploy/1panel/.env.example` 是生产失败关闭模板，所有 `CHANGE_ME` 必须替换。
- `VITE_*` 在前端构建时固化到浏览器产物，绝不能包含密钥。

## 2. 启动与数据库

| 变量 | 默认值 | 生产要求 | 说明 |
|---|---|---|---|
| `APP_ENV` | `development` | `production` | `development/test/staging/production` |
| `DATABASE_URL` | `sqlite:///./luheng-dev.db` | PostgreSQL URL | SQLAlchemy 连接串 |
| `AUTO_CREATE_SCHEMA` | 非生产 `true` | 必须 `false` | 仅开发/测试允许 ORM 建表 |
| `SEED_DEMO_DATA` | 开发 `true`，其他 `false` | 必须 `false` | 导入 AMC 虚构样本 |
| `CORS_ORIGINS` | 两个 localhost 来源 | 明确 HTTPS 来源 | 逗号分隔，不允许 `*` |

生产 PostgreSQL 表结构只由 `backend/migrations/` 管理。若 `SEED_DEMO_DATA=true` 且数据库已有非演示租户，应用会拒绝混入样本。

## 3. 身份与授权

| 变量 | 默认值 | 敏感 | 说明 |
|---|---|---:|---|
| `AUTH_MODE` | `development` | 否 | `auto/oidc/shared-secret/development`；生产必须 `oidc` |
| `ALLOW_DEV_HEADER_AUTH` | 仅开发为 `true` | 否 | 允许 `X-Actor-ID`；生产必须关闭 |
| `ALLOW_DEV_TOKEN` | 仅开发为 `true` | 否 | 开发令牌入口；生产必须关闭 |
| `AUTH_JWT_SECRET` | 空 | 是 | 共享密钥 JWT；OIDC 模式不需要 |
| `OIDC_ISSUER` | 空 | 否 | 令牌签发方，生产必填 |
| `OIDC_AUDIENCE` | 空 | 否 | API audience，生产必填 |
| `OIDC_JWKS_URL` | 空 | 否 | 无内嵌凭证的 HTTPS JWKS URL |
| `OIDC_ALLOWED_ALGORITHMS` | `RS256` | 否 | 非对称算法白名单；拒绝 `HS*` 和 `none` |
| `OIDC_REQUIRED_CLAIMS` | `sub,exp,iat` | 否 | 只能追加，不能移除三个核心声明 |
| `OIDC_TENANT_CLAIM` | 空 | 否 | 可选租户授权声明名 |
| `OIDC_LEEWAY_SECONDS` | `30` | 否 | 0—300 秒 |
| `OIDC_JWKS_CACHE_SECONDS` | `300` | 否 | 60—86400 秒 |
| `OIDC_JWKS_TIMEOUT_SECONDS` | `5` | 否 | 1—30 秒 |
| `OIDC_ALLOW_INSECURE_JWKS` | `false` | 否 | 仅封闭测试网络允许 HTTP |
| `RUNTIME_JWT_SECRET` | 回退 `AUTH_JWT_SECRET` | 是 | Agent 工具短时 Grant；生产至少 32 字符且独立 |

生产启动阶段会同时验证 OIDC、开发认证开关、数据库、CORS 和 Runtime 密钥，任一项不安全都会拒绝启动。

## 4. 异步作业与 Worker

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JOB_EXECUTION_MODE` | `inline` | 本地可内联；Compose 使用 `external` |
| `JOB_BROKER_BACKEND` | `database` | PostgreSQL 部署使用 `postgres-notify` |
| `JOB_BROKER_CHANNEL` | `luheng_jobs` | PostgreSQL 通知频道 |
| `JOB_LEASE_SECONDS` | `60` | 作业租约时长，范围由代码限制 |
| `WORKER_STALE_SECONDS` | `30` | Worker 心跳陈旧阈值 |
| `WORKER_ID` | 随机 | 部署应设置稳定、唯一实例 ID |
| `WORKER_QUEUES` | `default` | 逗号分隔队列列表 |
| `WORKER_POLL_SECONDS` | `1` | 0.1—60 秒；通知失败时的轮询间隔 |

## 5. 密钥存储

| 变量 | 默认值 | 敏感 | 说明 |
|---|---|---:|---|
| `SECRET_STORE_BACKEND` | `reference-only` | 否 | `reference-only/local-envelope/aws-secrets-manager` |
| `SECRET_MASTER_KEY` | 空 | 是 | `local-envelope` 的 32 字节 URL-safe Base64 密钥 |
| `SECRET_MASTER_KEY_VERSION` | `local-v1` | 否 | 当前主密钥版本 |
| `SECRET_PREVIOUS_KEYS` | 空 | 是 | 旧版本到密钥的 JSON，仅轮换窗口使用 |
| `AWS_REGION` | 空 | 否 | Secrets Manager 区域 |
| `AWS_DEFAULT_REGION` | 空 | 否 | 区域回退值 |
| `AWS_SECRET_PREFIX` | `luheng/fulfillops` | 否 | Secret 名称前缀 |
| `AWS_KMS_KEY_ID` | 空 | 否 | 可选客户托管 KMS Key |
| `AWS_SECRETS_MANAGER_ENDPOINT` | 空 | 可能 | 仅 LocalStack/受控测试端点 |

应用不读取静态 AWS Access Key 配置；云环境应使用实例或工作负载角色。

## 6. 模型 Gateway

| 变量 | 默认值 | 敏感 | 说明 |
|---|---|---:|---|
| `ENABLE_LIVE_MODEL_CALLS` | `false` | 否 | 部署级真实模型总开关 |
| `MODEL_EGRESS_ALLOWLIST` | 空/部署指定 | 否 | 逗号分隔允许主机 |
| `MODEL_ALLOWED_MODELS` | `deepseek-flash,deepseek-v4-pro` | 否 | 模型白名单 |
| `MODEL_MAX_COST_USD_PER_CALL` | `0.25` | 否 | 单次调用上限 |
| `MODEL_DAILY_COST_USD_PER_TENANT` | `1.0` | 否 | 每租户 UTC 自然日保守费用上限 |
| `MODEL_COST_CEILING_USD_PER_M_TOKENS` | `20` | 否 | 预算预留的保守单价上限 |
| `MODEL_ALLOW_INSECURE_HTTP` | `false` | 否 | 仅封闭测试环境可启用 |
| `MODEL_ALLOW_PRIVATE_EGRESS` | `false` | 否 | 仅受控企业网关可启用 |
| `DEEPSEEK_API_KEY` | 空 | 是 | 环境注入回退；优先租户密钥后端 |

即使部署开关开启，租户配置仍需选择 `live-provider`、通过连接测试并由管理员明确确认。

## 7. DeepSeek Harness

| 变量 | 默认值 | 敏感 | 说明 |
|---|---|---:|---|
| `FULFILLOPS_ENABLE_DSH_RUNTIME` | `false` | 否 | 官方 Python SDK Runtime 总开关 |
| `FULFILLOPS_DSH_ROOT` | `/tmp/luheng-fulfillops-dsh` | 否 | Runtime 状态目录 |
| `FULFILLOPS_INTERNAL_URL` | `http://127.0.0.1:8000` | 否 | Worker 调用 API 的内部地址 |
| `FULFILLOPS_MCP_BASE_URL` | 空 | 否 | MCP 子进程内部 API 地址，由 Runtime 注入 |
| `FULFILLOPS_MCP_TOKEN` | 空 | 是 | 租户/会话/范围绑定的短时工具令牌 |
| `DSH_MODEL` | `deepseek-chat` | 否 | SDK 模型回退值 |
| `DEEPSEEK_BASE_URL` | 空 | 否 | SDK Endpoint 回退值 |

构建参数 `INSTALL_HARNESS=true` 才安装可选 SDK；关闭运行开关时仍不得启动真实 Runtime。

## 8. 支付与账务

| 变量 | 默认值 | 敏感 | 说明 |
|---|---|---:|---|
| `ENABLE_PAYMENT_SANDBOX` | `false` | 否 | 允许生成模拟签名回执 |
| `PAYMENT_SANDBOX_SECRET` | 空 | 是 | 沙箱 Provider HMAC 密钥 |
| `PAYMENT_WEBHOOK_TOLERANCE_SECONDS` | `300` | 否 | 允许 60—900 秒 |
| `ALLOW_LEGACY_PAYMENT_MATCH` | `false` | 否 | 旧单步匹配兼容开关；生产保持关闭 |

正式 Provider 密钥通过租户级密钥后端配置，不应放入前端或公共环境文件。

### 联系策略

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CONTACT_MAX_ATTEMPTS_PER_CASE_DAY` | `3` | 单案件每 UTC 自然日允许的最大联系任务数 |
| `CONTACT_WINDOW_START_UTC_HOUR` | `1` | 允许创建任务的 UTC 开始小时（含） |
| `CONTACT_WINDOW_END_UTC_HOUR` | `13` | 允许创建任务的 UTC 结束小时（不含） |

生产必须根据租户司法管辖区和合规批准设置窗口；当前契约只创建受控任务，不直接拨号。

## 9. 前端构建

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | 浏览器 API 根路径；生产建议同源 |
| `VITE_ENABLE_DEV_AUTH` | `false` | 仅显式开发构建发送 `X-Actor-ID: Terry` |
| `VITE_OIDC_AUTHORITY` | 空 | IdP authorization/token 端点根地址 |
| `VITE_OIDC_CLIENT_ID` | 空 | 公共浏览器客户端 ID |
| `VITE_OIDC_REDIRECT_URI` | 当前页 | 登录回调 URI |
| `VITE_OIDC_SCOPE` | `openid profile email offline_access` | 请求的 OIDC Scope |
| `VITE_OIDC_AUDIENCE` | 空 | 可选 API audience |
| `VITE_OIDC_LOGOUT_URI` | 空 | 退出端点；未设置时仅清理本地会话 |

Vite 自带的 `import.meta.env.DEV` 在 `npm run dev` 时也启用开发身份。生产 `npm run build` 中 `DEV=false`。

## 10. 1Panel/Compose 专用变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HTTP_PORT` | `18080` | 只绑定宿主机 `127.0.0.1` |
| `POSTGRES_DB` | `luheng` | PostgreSQL 数据库名 |
| `POSTGRES_USER` | `luheng` | PostgreSQL 用户 |
| `POSTGRES_PASSWORD` | 必填 | 数据库密码，需与 URL 一致 |
| `INSTALL_HARNESS` | `false` | 构建可选 Harness 依赖 |
| `INSTALL_AWS` | `false` | 构建可选 boto3 依赖 |

## 11. 生产最小配置检查

下列示例只展示字段，不可原样使用：

```dotenv
APP_ENV=production
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@postgres:5432/DB
AUTO_CREATE_SCHEMA=false
SEED_DEMO_DATA=false
AUTH_MODE=oidc
ALLOW_DEV_HEADER_AUTH=false
ALLOW_DEV_TOKEN=false
OIDC_ISSUER=https://idp.example.com/
OIDC_AUDIENCE=luheng-fulfillops
OIDC_JWKS_URL=https://idp.example.com/.well-known/jwks.json
CORS_ORIGINS=https://repayguard.example.com
RUNTIME_JWT_SECRET=<至少 32 字符随机值>
SECRET_STORE_BACKEND=local-envelope
SECRET_MASTER_KEY=<URL-safe Base64 32 字节密钥>
ENABLE_LIVE_MODEL_CALLS=false
ENABLE_PAYMENT_SANDBOX=false
FULFILLOPS_ENABLE_DSH_RUNTIME=false
```
