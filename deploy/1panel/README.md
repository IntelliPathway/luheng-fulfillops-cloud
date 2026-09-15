# ECS + 1Panel 部署

该目录提供生产安全模板：Nginx Web、FastAPI、独立 Worker 和 PostgreSQL 四个容器。只有 Web 绑定宿主机 `127.0.0.1:18080`，公网 HTTPS 由 1Panel 反向代理终止。

## 部署前结论

本栈调用远程模型，不在 ECS 运行本地大模型。

| 档位 | vCPU | 内存 | 系统盘 | 适用范围 |
|---|---:|---:|---:|---|
| 不建议 | 1–2 | 2 GB | 40 GB | 1Panel、PostgreSQL 和 Worker 会争抢内存 |
| 最低验收 | 2 | 4 GB + 2 GB Swap | 60 GB SSD | 小流量功能验收 |
| 推荐 | 4 | 8 GB | 100 GB SSD | 稳定测试与早期试运行 |
| 扩展 | 8 | 16 GB+ | 独立数据盘 | 多 Worker 或较高并发 |

当前目标 ECS 为 `2C2G/40G`，不满足最低部署门槛。本说明可以先用于准备配置，但不要在该实例强行启动完整栈。

```bash
./scripts/ecs-capacity-check.sh /opt/1panel
```

退出码 `2` 表示失败，`0` 表示达到“最低验收”或“推荐”。

## 生产默认值

`docker-compose.yml` 默认：

- `APP_ENV=production`；
- `AUTH_MODE=oidc`；
- `ALLOW_DEV_HEADER_AUTH=false`；
- `ALLOW_DEV_TOKEN=false`；
- `SEED_DEMO_DATA=false`；
- `AUTO_CREATE_SCHEMA=false`；
- 真实模型、Harness 和支付沙箱关闭。

代码还会在启动时复核这些条件。不要通过改回开发认证绕过 IdP 接入。

## 部署步骤

1. 升配 ECS，并在服务器执行容量检查。
2. 在普通应用目录拉取已通过 CI 的目标 Git 提交。
3. 复制 `deploy/1panel/.env.example` 为 `deploy/1panel/.env`。
4. 替换所有 `CHANGE_ME`，填写企业 OIDC 和唯一 HTTPS 站点来源。
5. 用 `docker compose config --quiet` 验证变量完整。
6. 在 1Panel“容器 → 编排”中导入本 Compose，构建并启动。
7. 在 1Panel“网站 → 反向代理”中把域名代理到 `http://127.0.0.1:18080`。
8. 申请证书、强制 HTTPS，不开放 PostgreSQL 或 API 内部端口。
9. 运行首租户初始化命令。
10. 验证 OIDC、租户隔离、API readiness、Worker 心跳和业务冒烟。

配置验证示例：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml config --quiet
```

密钥生成示例：

```bash
openssl rand -hex 32
openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
```

三个用途的密钥必须互不相同。优先使用 1Panel 密钥变量或云 KMS 注入，不要写入 Git。

## 初始化首租户

生产不导入 `TENANT_A/TENANT_B` 或演示用户。API ready 后执行：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml exec api \
  python -m app.provision_cli \
  --tenant-id CUSTOMER_001 \
  --tenant-name '客户一' \
  --user-id 'OIDC_SUBJECT' \
  --email 'owner@example.com' \
  --display-name '首位管理员'
```

`--user-id` 必须等于企业 IdP 令牌中的 `sub`。命令只创建租户、用户、管理员成员关系和审计事件，不创建业务样本。

## 验证

```bash
curl --fail https://YOUR_DOMAIN/api/v1/health/live
curl --fail https://YOUR_DOMAIN/api/v1/health/ready
```

登录后继续检查：

- `/api/v1/auth/session` 返回正确 subject、租户和数据库角色；
- `/api/v1/jobs/queue/health` 至少有一个健康 Worker；
- 跨租户请求返回 403/404；
- 真实模型、支付沙箱和 Harness 仍为关闭状态；
- 保护、对账和履约流程保持 Maker–Checker。

浏览器通用 OIDC Authorization Code + PKCE 流程尚未内置。部署团队必须按最终 IdP 增加登录/回调适配或受信身份代理；在此之前不能把该环境作为公开生产系统。

## 发布、回滚与备份

升级前：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml exec -T postgres \
  pg_dump -U luheng -d luheng -Fc > luheng-before-upgrade.dump
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml build
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml up -d
```

数据库迁移是前向、事务化执行。回滚应用镜像不等于回滚数据结构；恢复数据库属于破坏性操作，必须停写、核对备份与目标数据库后单独执行。

完整故障排查、备份要求和发布后检查见 [运维手册](../../docs/operations-runbook.md)，全部变量见 [配置参考](../../docs/configuration.md)。
