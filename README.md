# 履衡 AI · FulfillOps Cloud

面向资产履约运营的多租户 AI 工作空间。当前版本为 `v0.13.0`，包含 React/Vite 前端、FastAPI 业务 API、独立异步 Worker、PostgreSQL 账务与审计数据，以及受控的 Agent/模型/支付接入边界。

> 当前仓库是可联调开发版，不是“配置密钥即可直接商用”的成品。真实外呼、真实支付和真实模型调用均默认关闭；企业上线还需要 IdP、Provider、监控、备份恢复和目标 ECS 验收。

## 先选运行模式

| 模式 | 数据 | 身份 | 外部动作 | 适用场景 |
|---|---|---|---|---|
| 本地 Docker 开发 | PostgreSQL + AMC 虚构样本 | 开发身份 | 默认全部关闭 | 功能体验、联调、开发 |
| 本地前端演示 | 浏览器脱敏样本 | 本地演示身份 | 不执行 | UI 评审、无后端预览 |
| Sites 演示 | 浏览器脱敏样本 | 站点访问控制 | `/api/*` 明确返回 503 | 纯前端展示 |
| ECS/1Panel 生产模板 | PostgreSQL，不导入样本 | 企业 OIDC | 按服务逐项启用 | 测试环境、后续生产 |

生产模板不会自动退回开发身份，也不会自动导入 `TENANT_A/TENANT_B`。认证失败、权限不足或 API 配置错误时，前端会阻断操作；只有真正无法连接 API，或 Sites 明确声明演示模式时，才进入离线沙箱。

## 五分钟开机：Docker 开发环境

### 前置条件

- Git
- Docker Engine 24+ 与 Docker Compose v2
- 至少 4 GB 可用内存
- 本机端口 `4177`、`8000`、`5432` 未被占用

### 启动

```bash
git clone https://github.com/IntelliPathway/luheng-fulfillops-cloud.git
cd luheng-fulfillops-cloud
docker compose up --build -d
docker compose ps
```

首次构建会安装前后端依赖。所有服务健康后访问：

- Web：<http://localhost:4177>
- API 文档：<http://localhost:8000/api/docs>
- API 就绪检查：<http://localhost:8000/api/v1/health/ready>
- API 存活检查：<http://localhost:8000/api/v1/health/live>

验证命令：

```bash
curl --fail http://localhost:8000/api/v1/health/ready
curl --fail -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: Terry' \
  http://localhost:8000/api/v1/auth/session
```

开发栈会显式设置 `APP_ENV=development`、`SEED_DEMO_DATA=true` 和开发头身份。默认用户如下：

| 用户 ID | 角色 | 用途 |
|---|---|---|
| `Terry` | 管理员 | 浏览器默认开发身份 |
| `test-user` | 管理员 | Maker–Checker 独立复核 |
| `test-operator` | 运营人员 | 创建提案和日常运营 |
| `test-viewer` | 观察员 | 只读验证 |

### 停止、重启和查看日志

```bash
docker compose stop
docker compose start
docker compose logs -f api worker web
```

保留数据并停止容器：

```bash
docker compose down
```

彻底删除本地数据库卷并恢复全新样本（不可恢复）：

```bash
docker compose down -v
docker compose up --build -d
```

## 不使用 Docker 的本地开发

后端要求 Python 3.12：

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
APP_ENV=development \
SEED_DEMO_DATA=true \
DATABASE_URL=sqlite:///./luheng-dev.db \
.venv/bin/uvicorn app.main:app --reload --port 8000
```

另开终端启动前端（Node.js 22）：

```bash
npm ci
npm run dev
```

Vite 开发模式允许开发身份；生产构建不会自动发送 `X-Actor-ID: Terry`。详细后端说明见 [backend/README.md](backend/README.md)。

## 开发验证

安装完前后端依赖后，从仓库根目录执行：

```bash
npm run verify
```

该命令依次执行 Ruff 代码检查与格式检查、后端 Pytest、前端领域/API/部署/Sites 契约测试，以及 Vite 生产构建。没有 PostgreSQL 时，本地 PostgreSQL 增量迁移专项会跳过；GitHub Actions 使用 PostgreSQL 17 执行完整迁移链。

## 产品体验主路径

1. 在“AI 与渠道”查看 Agent、模型、语音和电话的配置、连接测试、自测与启用门禁。
2. 创建清收活动，观察服务端对授权、策略、案件范围、预算和渠道状态的预检。
3. 在 Agent 运行详情查看判断依据、受控工具、等待条件和暂停/恢复提案。
4. 在异常中心处理保护事件；运营提交证据，由不同管理员复核，批准后只进入“待重新评估”。
5. 在履约计划提交外部协议引用与摘要，由独立管理员批准，再用验签回款驱动期次分摊。
6. 在回款与佣金页验证回执验签、幂等、异常对账、退款冲销、计佣、结算与实收证据链。
7. 使用全局履衡 AI 查询运营和钱指标；任何高影响动作只生成提案，不直接改账或触达。

完整范围、角色权限、状态机和验收口径见 [产品功能规格](docs/product-functional-spec.md)。

## 生产部署前必读

ECS/1Panel 使用 [deploy/1panel/docker-compose.yml](deploy/1panel/docker-compose.yml)，但不要直接复制本地开发 Compose 到公网。生产模板默认：

- `APP_ENV=production`；
- PostgreSQL 版本化迁移，关闭 ORM 自动建表；
- 关闭演示种子、开发头身份和开发令牌；
- 强制企业 OIDC、明确 HTTPS CORS 来源和独立 Runtime 密钥；
- API、Worker 以 UID `10001` 非 root 运行；
- PostgreSQL/API 不映射公网端口，Web 只绑定 `127.0.0.1` 供 1Panel 反向代理。

部署后用幂等命令创建第一个租户管理员，`--user-id` 必须与 IdP 的 `sub` 完全一致：

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

当前目标 ECS 截图显示 `2 vCPU / 2 GiB / 40 GiB`，低于本栈最低门槛，不应部署完整四容器。最低验收建议 `2 vCPU / 4 GB / 60 GB SSD + 2 GB Swap`，推荐 `4 vCPU / 8 GB / 100 GB SSD`。容量检查与上线步骤见 [ECS/1Panel 部署说明](deploy/1panel/README.md)和[运维手册](docs/operations-runbook.md)。

## 文档导航

| 文档 | 面向对象 | 内容 |
|---|---|---|
| [产品功能规格](docs/product-functional-spec.md) | 产品、研发、测试、交付 | 功能边界、角色、流程、状态机和验收标准 |
| [系统架构](docs/architecture.md) | 架构师、研发、运维 | 组件、模块、数据流、关键决策与技术债 |
| [配置参考](docs/configuration.md) | 研发、运维 | 全部后端/前端环境变量和生产要求 |
| [运维手册](docs/operations-runbook.md) | 运维、交付 | 启停、发布、备份、升级、故障处理 |
| [安全策略](SECURITY.md) | 安全、研发、交付 | 信任边界、密钥、认证、漏洞报告和上线检查 |
| [贡献指南](CONTRIBUTING.md) | 开发者 | 分支、代码风格、测试和提交标准 |
| [变更记录](CHANGELOG.md) | 全体 | 版本级变化索引 |
| [v0.13 发布说明](RELEASE_v0.13.0.md) | 发布与验收 | 本轮变化、验证结果和已知限制 |

## 当前明确限制

- 浏览器端通用 OIDC Authorization Code + PKCE 流程尚未内置；生产接入必须由具体 IdP 适配或身份代理完成，前端不会伪造开发身份。
- 语音、电话和 Hermes Provider 仍以确定性沙箱适配器为主，不执行真实外呼或邮件。
- 支付沙箱默认关闭，且不连接真实收单机构或银行系统。
- DeepSeek Harness `python-sdk` 和模型 `live-provider` 均需部署开关、租户密钥、白名单、自测和管理员确认。
- 当前单体 API 和前端应用仍需继续按领域拆分；v0.13 先收敛生产安全、格式门禁和文档体系。

项目为私有业务代码仓库，尚未发布开源许可；未经授权不得按开源许可证分发。
