# 运维手册

## 1. 适用拓扑

本文针对 ECS + 1Panel + Docker Compose：Nginx Web、FastAPI、Worker 和 PostgreSQL 四个容器。Sites 仅是静态演示，不适用本手册。

## 2. 容量门禁

| 档位 | CPU | 内存 | 磁盘 | 结论 |
|---|---:|---:|---:|---|
| 当前目标机 | 2 vCPU | 2 GiB | 40 GiB | 不部署完整栈 |
| 最低验收 | 2 vCPU | 4 GB + 2 GB Swap | 60 GB SSD | 低流量测试 |
| 推荐 | 4 vCPU | 8 GB | 100 GB SSD | 稳定测试/早期试运行 |

部署前执行：

```bash
./scripts/ecs-capacity-check.sh /opt/1panel
```

退出码 `2` 表示不满足最低条件。除了脚本结果，还必须确认数据库增长、备份保留、峰值并发和日志空间。

## 3. 首次部署

1. 在 `/opt/1panel/apps/luheng-fulfillops` 拉取仓库。
2. 将 `deploy/1panel/.env.example` 复制为 `deploy/1panel/.env`。
3. 替换全部 `CHANGE_ME`，配置企业 OIDC 和唯一 HTTPS 域名。
4. 运行 Compose 配置检查。
5. 在 1Panel 导入 `deploy/1panel/docker-compose.yml`，项目目录指向仓库根目录。
6. 仅把 `127.0.0.1:18080` 配置为 1Panel 反向代理上游。
7. 申请证书并强制 HTTPS。
8. 初始化首租户管理员。
9. 完成健康、权限、租户隔离和核心业务冒烟。

配置检查：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml config --quiet
```

生成独立随机密钥：

```bash
openssl rand -hex 32
openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
```

不要把终端输出粘贴到工单、聊天或 Git。

## 4. 启动与状态确认

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml up -d --build
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml ps
```

容器内验证：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready').read().decode())"
```

公网验证：

```bash
curl --fail --silent --show-error https://YOUR_DOMAIN/api/v1/health/live
curl --fail --silent --show-error https://YOUR_DOMAIN/api/v1/health/ready
```

`live` 只说明进程存活；`ready` 会真实查询数据库。Worker 健康由容器心跳检查和登录后的 `/api/v1/jobs/queue/health` 综合判断。

## 5. 初始化首租户管理员

生产不导入演示用户。API 健康后执行：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml exec api \
  python -m app.provision_cli \
  --tenant-id CUSTOMER_001 \
  --tenant-name '客户一' \
  --user-id 'OIDC_SUBJECT' \
  --email 'owner@example.com' \
  --display-name '首位管理员' \
  --role admin
```

命令幂等：相同输入重复执行返回同一成员关系；同 ID 不同名称、邮箱或角色会失败，不会静默覆盖。该命令不创建资产包、案件、回款或任何 AMC 样本。

## 6. 日常操作

查看日志：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml logs --since 30m api worker web postgres
```

重启单个无状态服务：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml restart api
```

不要无原因重启 PostgreSQL；先确认正在运行的作业和备份状态。

## 7. 发布升级

1. 记录当前 Git commit 和镜像标签。
2. 备份 PostgreSQL，并验证备份文件非空。
3. 拉取已通过 CI 的目标提交。
4. 执行 `docker compose config --quiet`。
5. 构建镜像并启动。
6. 检查迁移记录、API readiness、Worker 心跳和前端版本。
7. 执行本次发布说明要求的冒烟。

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml build
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml up -d
```

数据库迁移为前向迁移。回滚应用镜像不等于回滚表结构；任何数据结构回退必须单独设计和演练。

## 8. 备份与恢复

备份：

```bash
docker compose --env-file deploy/1panel/.env \
  -f deploy/1panel/docker-compose.yml exec -T postgres \
  pg_dump -U luheng -d luheng -Fc > luheng-$(date +%Y%m%d-%H%M%S).dump
```

最低要求：

- 备份文件离机保存；
- 文件加密并限制访问；
- 记录数据库版本、应用 commit 和生成时间；
- 定期在隔离环境执行恢复演练；
- 恢复前停止写入并二次确认目标数据库。

本仓库不会自动执行恢复，因为恢复会覆盖数据。正式环境需依据业务确定 RPO/RTO 后配置计划任务和告警。

## 9. 常见故障

| 现象 | 检查 | 处理 |
|---|---|---|
| API 容器立即退出 | `docker compose logs api` | 修复生产配置门禁；不要改回开发认证 |
| `ready` 失败、`live` 正常 | PostgreSQL 健康、URL、迁移 | 先恢复数据库连接，再重启 API |
| 前端显示“需要企业身份” | OIDC Token、成员关系、租户头 | 校对 IdP `sub` 与 provision 命令 |
| 前端显示“API 尚未就绪” | API HTTP 状态与错误详情 | 修复服务端配置，不在浏览器本地写入 |
| Worker 降级 | Worker 日志、心跳、租约 | 修复 Worker；过期作业会按预算恢复 |
| 模型真实模式不可选 | 部署开关、白名单、租户密钥、自测 | 逐项补齐，不绕过门禁 |
| 回执未进入指标 | 签名、时间窗、匹配状态 | 在回执复核工作台处理，不直接改账 |
| 磁盘快速增长 | PG 数据、日志、备份 | 清理过期离机备份策略；不要直接删除 PG 文件 |

## 10. 发布后检查

- [ ] HTTPS 和证书链正常；
- [ ] 只有 80/443 对外，数据库和 API 管理端口未暴露；
- [ ] `APP_ENV=production`，开发认证和演示种子关闭；
- [ ] `/health/live` 与 `/health/ready` 正常；
- [ ] OIDC 登录和跨租户拒绝行为正确；
- [ ] API/Worker 使用非 root 用户；
- [ ] Worker 心跳正常，无陈旧作业；
- [ ] 模型、Harness、支付沙箱保持预期关闭状态；
- [ ] 备份已生成并可在隔离环境恢复；
- [ ] Git commit、镜像、迁移版本和验收结果已记录。
