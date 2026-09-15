# ECS + 1Panel 部署

该部署栈运行四个容器：Nginx 静态前端、FastAPI、独立 Worker 和 PostgreSQL。仅前端绑定到宿主机 `127.0.0.1:18080`，PostgreSQL 与 API 不直接暴露公网；公网 HTTPS 由 1Panel 网站反向代理终止。

## 容量门槛

本栈调用远程 DeepSeek，不在 ECS 上运行本地大模型。

| 档位 | vCPU | 内存 | 系统盘 | 公网带宽 | 适用范围 |
|---|---:|---:|---:|---:|---|
| 不建议 | 1–2 | 2 GB | 40 GB | 1–3 Mbps | 1Panel、数据库和 Worker 容易争抢内存 |
| 最低验收 | 2 | 4 GB | 60 GB SSD | 3 Mbps | 小流量功能验收，建议 2 GB Swap |
| 推荐 | 4 | 8 GB | 100 GB SSD | 5 Mbps+ | 稳定测试与早期试运行 |
| 扩展 | 8 | 16 GB+ | 独立数据盘 | 10 Mbps+ | 多 Worker、较高并发或长轨迹任务 |

先在 ECS 上运行只读检查：

```bash
./scripts/ecs-capacity-check.sh /opt/1panel
```

脚本退出码 `2` 表示不满足最低部署条件；`0` 表示可部署，并会标明“最低验收”或“推荐”。评估还应结合峰值并发、数据库增长、备份保留期和是否启用 Harness SDK。

## 1Panel 部署步骤

1. 在 1Panel 创建普通目录，例如 `/opt/1panel/apps/luheng-fulfillops`，拉取本仓库。
2. 复制 `deploy/1panel/.env.example` 为同目录 `.env`，替换所有 `CHANGE_ME`。数据库密码只使用字母、数字、下划线和连字符，并在 `POSTGRES_PASSWORD` 与 `DATABASE_URL` 中保持一致。
3. 生成三个互不相同的密钥：

   ```bash
   openssl rand -hex 32
   openssl rand -hex 32
   openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
   ```

4. 在 1Panel 的“容器 → 编排”中选择 `deploy/1panel/docker-compose.yml`，项目目录设为仓库根目录或确保 Compose 能读取旁边的 `.env`，执行构建并启动。
5. 确认四个服务健康：`postgres`、`api`、`worker`、`web`。
6. 在“网站 → 反向代理”中新建域名，代理到 `http://127.0.0.1:18080`，申请 HTTPS 证书并启用强制 HTTPS。
7. 外网验收档必须在 1Panel 启用访问密码或 IP 白名单；不得把开发身份模式作为正式生产认证。
8. 访问 `/api/v1/health`、`/api/v1/jobs/queue/health`，并执行 v0.11 保护工作流与 v0.12 履约计划冒烟测试。

如果 1Panel 要求 Compose 文件与 `.env` 同目录，可在创建编排时把二者一并粘贴/上传，构建上下文仍需指向完整仓库。

## 切换正式生产认证

正式生产前，至少修改：

```dotenv
AUTH_MODE=oidc
ALLOW_DEV_HEADER_AUTH=false
ALLOW_DEV_TOKEN=false
OIDC_ISSUER=https://idp.example.com/
OIDC_AUDIENCE=luheng-fulfillops
OIDC_JWKS_URL=https://idp.example.com/.well-known/jwks.json
OIDC_TENANT_CLAIM=tenant_ids
```

同时移除 1Panel 临时访问密码，改为企业 SSO；启用真实模型前还需独立配置租户密钥、出网白名单、成本上限和管理员确认。不要在 1Panel 编排文本、Git 或聊天中保存真实密钥，优先使用 1Panel 密钥变量或云 KMS 注入。

## 发布、回滚与备份

升级前先备份 PostgreSQL，再构建新镜像。数据库迁移是前向、事务化执行，回滚应用镜像不等于回滚数据结构；重大升级应先在测试 ECS 上演练。

```bash
docker compose --env-file deploy/1panel/.env -f deploy/1panel/docker-compose.yml exec -T postgres \
  pg_dump -U luheng -d luheng -Fc > luheng-before-upgrade.dump

docker compose --env-file deploy/1panel/.env -f deploy/1panel/docker-compose.yml build
docker compose --env-file deploy/1panel/.env -f deploy/1panel/docker-compose.yml up -d
docker compose --env-file deploy/1panel/.env -f deploy/1panel/docker-compose.yml ps
```

保留上一个 Git tag 与镜像。恢复数据库属于破坏性操作，必须在停写、核对备份文件与目标数据库后单独执行。
