# 自托管企业身份服务

身份服务模板：Keycloak 26.7.5 + 独立 PostgreSQL 17。公开客户端使用 PKCE S256，禁止密码直授和隐式授权；生产 API 只校验非对称签名与 issuer/audience，业务角色仍来自平台成员关系。

1. 按 [企业标准接入](../../docs/enterprise-onboarding.md) 生成配置包。
2. 复制 `.env.example` 到 `.env`，填实际身份域名、配置目录及独立数据库/管理密码。
3. 验证并启动：

```bash
docker compose --env-file deploy/identity/.env -f deploy/identity/docker-compose.yml config --quiet
docker compose --env-file deploy/identity/.env -f deploy/identity/docker-compose.yml up -d
```

4. 1Panel 以身份域名代理 `127.0.0.1:18088`，强制 HTTPS，覆盖转发来源/协议/Host 头；不要直接公开容器端口。身份管理入口限制运维访问。模板不是容量评估：身份数据库和 JVM 额外占用资源，应重新核对服务器容量。
5. Realm 导入仅用于首次初始化；已有 realm 不会被 `--import-realm` 覆盖。后续变更通过管理端版本化执行并备份数据库。
6. 创建两名不同审核人的账号，设置初次密码变更及企业 MFA 策略，取得实际 `sub` 并执行平台成员开通。模板不创建默认用户或密码。
7. API 配置使用生成的公开变量，浏览器配置使用生成的 runtime；端到端核对登录、刷新、登出、跨租户拒绝和撤权。

管理密码不进入前端、Realm JSON 或 Git。备份身份数据库和 realm/client 配置；IdP 不可用时应阻断登录，不能启用开发认证替代。

官方依据：[容器运行](https://www.keycloak.org/server/containers)、[反向代理](https://www.keycloak.org/server/reverseproxy)、[客户端与 audience](https://www.keycloak.org/docs/26.7.5/server_admin/)。本模板未在实际服务器启动，不宣称生产身份服务已运行。
