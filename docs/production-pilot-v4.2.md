# v4.2 · 后端接入与试点预检

本版交付可接入独立业务后端的 Sites 前端、只读试点预检与可复现验收测试。真实租户生产试点仍需企业资源，未配置时 Sites 明确保持演示模式。

## 部署职责

| 部署位置 | 职责 |
|---|---|
| Sites | React 前端、公开登录配置、安全响应头；不存密钥、不代理任意业务请求 |
| ECS/1Panel | FastAPI、独立 Worker、私有 PostgreSQL；迁移、账簿、审计和受控 Provider |
| 企业 IdP | 公共浏览器客户端与 PKCE；服务端验证 issuer/audience/JWKS，数据库成员关系裁决权限 |

## Sites 接入参数

通过 Sites 环境变量配置以下**公开参数**，保存后重新发布；禁止放入客户端密钥、访问 Token 或任何 Provider 密钥。

| 参数 | 内容 |
|---|---|
| `PUBLIC_API_BASE_URL` | 业务 API 的 HTTPS `/api/v1` 地址 |
| `PUBLIC_OIDC_AUTHORITY` | 企业 IdP HTTPS 标识地址 |
| `PUBLIC_OIDC_AUTHORIZATION_ENDPOINT` | IdP 授权码入口 |
| `PUBLIC_OIDC_TOKEN_ENDPOINT` | 支持公共客户端 PKCE/CORS 的 Token 入口 |
| `PUBLIC_OIDC_LOGOUT_ENDPOINT` | IdP 登出入口；按实际 IdP 验证参数兼容性 |
| `PUBLIC_OIDC_CLIENT_ID` | 公共浏览器客户端 ID |
| `PUBLIC_OIDC_AUDIENCE` | 业务 API audience |

后端 `CORS_ORIGINS` 与 IdP 的 redirect/CORS allowlist 必须包含实际 Sites 地址。运行时参数缺失或非法时阻断启动；已配置业务后端的网络故障不能退回离线写入。CSP 仅放行配置中的 HTTPS 来源。公共参数通过 `/runtime-config.json` 提供，无需重新编译前端。

未设置 `PUBLIC_API_BASE_URL` 时保留 Sites 演示，不虚构业务 API。前端直接向配置的 API 发起认证请求，Sites `/api/*` 仍返回明确的 503 演示响应。

## 后端开通

1. 使用现有 ECS/1Panel Compose、HTTPS 反向代理和生产环境配置；生产禁用开发认证、演示播种与 ORM 自动建表。
2. 使用 `python -m app.provision_cli` 幂等配置真实租户和两个不同的 IdP 管理员 sub。
3. 在组织设置中独立复核租户激活依据。配置服务后完成已有的测试、沙箱、审批门禁。
4. 更新 `PILOT_DEPLOYMENT_REVISION`，完成四项外部验收证据与独立复核。
5. 执行 `python -m app.pilot_cli --tenant-id <真实租户ID>`。退出码 0 表示当前预检 ready，1 表示检查阻断，2 表示配置/连接失败。此命令只读，不建表、不迁移、不发送外部请求。

预检联合验证迁移记录、Worker 心跳与过期租约、两个有效管理员、租户激活和验收门禁。结果不启用真实调用，也不能替代业务演练。

## 验收与恢复

按 [试点验收操作](pilot-acceptance.md) 验证导入、审批、联系任务、回款、佣金和审计闭环。在隔离 PostgreSQL 中执行备份恢复并核对关键行数、账簿和审计摘要，记录实测 RPO/RTO；不可恢复覆盖生产库。

浏览器旅程执行 `npm run test:browser`，CI 会启动本地测试 API 与 Vite。使用测试身份和本地 SQLite，不代表真实 IdP 或生产 Provider 已通过。
