<p align="center">
  <img src="public/assets/repayguard-ai-mark.png" alt="履约智控 AI / RepayGuard AI Logo" width="112">
</p>

<h1 align="center">履约智控 AI · RepayGuard AI</h1>

<p align="center"><strong>面向资产履约与回款运营的可信 AI 控制平台</strong></p>

| 当前版本 | 前端 | 业务服务 | 数据与任务 | 生产模板 |
|---|---|---|---|---|
| `v5.0.0` | React 19 + Vite 6 | FastAPI + 受控 Agent Gateway | PostgreSQL + 独立 Worker | ECS + 1Panel Compose |

> AI 负责查询、解释和生成提案；权限、策略、保护状态、双人复核与账务内核拥有最终决定权。真实外呼、支付和模型调用默认关闭。

## 一分钟理解

```mermaid
flowchart LR
    Data["CSV 预演 / 权威资产目录"] --> Core["履约智控内核"]
    Core --> Agent["AI 查询与行动提案"]
    Core --> Guard["权限 / 保护 / 双人复核"]
    Core --> Ledger["回款 / 分期 / 佣金证据"]
```

| 问题 | 平台怎样处理 |
|---|---|
| 哪些案件可以行动？ | 策略、授权、保护状态和渠道门禁共同预检 |
| AI 可以做什么？ | 查事实、解释原因、规划步骤、生成待审提案 |
| 谁能批准高影响操作？ | 与提案人不同的管理员 |
| 钱指标从哪里来？ | 已验签回执与不可变账簿，不读页面模拟值 |
| 业务数据怎样进入？ | CSV 先预演和逐行校验，再由另一管理员确认；不覆盖已有案件 |
| 在线页面读取什么？ | 服务端分页目录及账簿聚合；API 失败时阻断，不回退到样本 |
| 失败后怎样恢复？ | 数据库租约、Worker 心跳、重试预算和审计事件 |

## 三步开机

前置条件：Docker Engine 24+、Docker Compose v2、至少 4 GB 可用内存；端口 `4177`、`8000`、`5432` 未占用。

```bash
git clone https://github.com/IntelliPathway/luheng-fulfillops-cloud.git
cd luheng-fulfillops-cloud
docker compose up --build -d
```

确认服务：

```bash
docker compose ps
curl --fail http://localhost:8000/api/v1/health/ready
```

打开：

| 入口 | 地址 | 用途 |
|---|---|---|
| Web | <http://localhost:4177> | 产品工作台 |
| API Docs | <http://localhost:8000/api/docs> | OpenAPI 调试 |
| Readiness | <http://localhost:8000/api/v1/health/ready> | 数据库与迁移就绪 |
| Liveness | <http://localhost:8000/api/v1/health/live> | 进程存活 |

开发样本身份：

| 用户 ID | 角色 | 典型操作 |
|---|---|---|
| `Terry` | 管理员 | 默认浏览与审批 |
| `test-user` | 管理员 | 独立复核 |
| `test-operator` | 运营人员 | 创建提案 |
| `test-viewer` | 观察员 | 只读验收 |

<details>
<summary><strong>不用 Docker：本地前后端分别启动</strong></summary>

后端要求 Python 3.12：

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
APP_ENV=development SEED_DEMO_DATA=true \
DATABASE_URL=sqlite:///./luheng-dev.db \
.venv/bin/uvicorn app.main:app --reload --port 8000
```

另开终端启动 Node.js 22 前端：

```bash
npm ci
npm run dev
```

</details>

## 体验主路径

企业接入主路径：**企业登录与开通 → 案件导入 → 委托核验 → 材料关联 → 付款与退款核对 → 独立审核 → 当前验收报告**。无退款填 0；不能用净额一致替代付款、退款分别一致。

```mermaid
flowchart TD
    A["企业登录与原子开通"] --> B["案件导入与委托核验"]
    B --> C["材料关联与独立复核"]
    C --> D["付款退款分别核对"]
    D --> E["独立审核与当前报告"]
```

首家内部企业和后续企业复用 [企业标准接入](docs/enterprise-onboarding.md)。支持自托管身份服务、真实成员工作空间发现、原子双管理员开通及可下载标准模板。Sites 当前仍提供明确演示，正式服务部署状态与软件完成状态分别记录。


1. 在“资产包”打开导入中心，使用脱敏样本或 UTF-8 CSV 生成服务端预演。
2. 由另一名管理员核对摘要和错误报告后提交；新资产包以草稿策略进入系统。
3. 在资产包和案件页确认新记录进入服务端权威目录，并使用搜索、视图、排序和分页复核。
4. 在“AI 与渠道”完成配置、连接测试、自测和管理员启用。
5. 创建履约活动，并在异常、履约计划和回款页验证保护、签约、对账及计佣。

## 运行模式

| 模式 | 权威数据 | 身份 | 外部动作 | 适用场景 |
|---|---|---|---|---|
| 前端离线演示 | 浏览器脱敏样本 | 演示身份 | 不执行 | UI 评审 |
| Docker 开发 | PostgreSQL + AMC 虚构样本 | 开发身份 | 默认关闭 | 开发与联调 |
| Sites 演示 | 浏览器脱敏样本 | 站点访问控制 | `/api/*` 返回 503 | 静态展示 |
| ECS/1Panel | PostgreSQL，不导入样本 | 企业 OIDC | 逐项启用 | 测试与生产 |

认证失败、权限不足或已连通 API 报错时，浏览器不会静默转为可写演示。

## ECS / 1Panel 门槛

| 档位 | vCPU | 内存 | 磁盘 | 结论 |
|---|---:|---:|---:|---|
| 当前目标机 | 2 | 2 GB | 40 GB | 不部署完整四容器 |
| 最低验收 | 2 | 4 GB + 2 GB Swap | 60 GB SSD | 低流量测试 |
| 推荐 | 4 | 8 GB | 100 GB SSD | 稳定测试与早期试运行 |

```bash
./scripts/ecs-capacity-check.sh /opt/1panel
```

生产模板见 [ECS + 1Panel 部署说明](deploy/1panel/README.md)。它默认使用企业 OIDC、版本化迁移、非 root 容器、私有 API/PostgreSQL，并关闭演示种子和真实 Provider。

## 工程验证

```bash
npm run verify
```

该命令依次执行 Ruff、Pytest、前端领域/API/品牌、文档链接、部署/Sites 契约测试和生产构建。GitHub Actions 另用 PostgreSQL 17 验证完整迁移链。

常用操作：

| 目标 | 命令 |
|---|---|
| 查看日志 | `docker compose logs -f api worker web` |
| 停止并保留数据 | `docker compose down` |
| 重启 | `docker compose up -d` |
| 导出单文件演示 | `python3 scripts/export-standalone.py` |

## 文档地图

```mermaid
flowchart TB
    Start["README · 开机与入口"] --> Product["产品功能规格"]
    Start --> Architecture["系统架构"]
    Start --> Delivery["配置 / 运维 / 安全"]
    Product --> Release["版本与验收"]
    Architecture --> Release
```

| 文档 | 读者 | 一句话用途 |
|---|---|---|
| [品牌与命名](docs/brand-guide.md) | 产品、设计、研发 | 中英文名、Logo、语气与使用规则 |
| [产品功能规格](docs/product-functional-spec.md) | 产品、研发、测试、交付 | 能力地图、角色、流程、规则和验收 |
| [系统架构](docs/architecture.md) | 架构、研发、运维 | 服务、数据、信任边界与技术债 |
| [配置参考](docs/configuration.md) | 研发、运维 | 全部环境变量及生产约束 |
| [运维手册](docs/operations-runbook.md) | 运维、交付 | 启停、发布、备份和故障处置 |
| [安全策略](SECURITY.md) | 安全、研发 | 密钥、认证、隔离和上线检查 |
| [贡献指南](CONTRIBUTING.md) | 开发者 | 代码风格、测试和提交标准 |
| [当前发布说明](RELEASE.md) | 发布与验收 | 当前版本摘要与历史发布索引 |
| [v1.5 进展评估](docs/v1.5-progress-assessment.md) | 产品、架构、交付 | 完成度、边界和后续优先级 |
| [人工确认备忘录](docs/manual-confirmation-memo.md) | 交付、合规、运维 | 联调与上线前待确认事项 |

## 当前边界

- 浏览器已具备 OIDC Authorization Code + PKCE 通用流程，企业 IdP 参数与成员映射待联调。
- 电话已具备验签事件契约；语音、SIP 和 Hermes Provider 的真实凭据与资源待提供。
- 支付沙箱默认关闭，不连接真实收单机构或银行。
- 真实模型调用需部署开关、租户密钥、出网白名单、自测和管理员确认。
- 仓库名、环境变量前缀、Webhook 头和 `fulfillops-safe` 等技术标识暂时保留，避免破坏现有集成；它们不是对外产品名。

项目为私有业务代码仓库，尚未发布开源许可证。

客户数据接入：[统一连接器协议](docs/integrations/customer-feed.md)。

材料与核验：[证据工作台使用及边界](docs/integrations/evidence-workspace.md)。

## 标准个人贷款机催 MVP（开发增量）

新增“标准贷款机催”入口，可在连接业务 API 后配置租户政策、进行案件准入、受控状态机对话、PTP 及账本核验联调。政策未配置默认阻断；时段、日上限、有效期和暂停状态由服务端检查，会话保留政策版本与快照。当前仅为显式沙箱，不发起真实外呼、不调用模型、不改变合同或账务；真实电话/媒体与机构本人核验适配器尚未接入。

范围、进度和依赖见 [MVP 计划](docs/standard-loan-machine-collection-mvp.md)。

Linphone 电话链路联调：[Asterisk + Linphone SIP 实验室](docs/integrations/linphone-sip-lab.md)。已提供隔离回声测试配置、固定分机 ARI 工具和崩溃/超时不重拨日志；尚未实测音频，不代表真实催收或公网手机外呼接通。

模型组合已接入 **AI 与渠道接入**：管理员选择 ASR / LLM / TTS、检查连接、运行单环节对照并查看实际延迟与权重版本，启用后运营可在 **AI 智能催收工作台** 的活动详情绑定和验证。当前验证范围为内部合成语句；Mac 模型宿主、电话媒体和真实业务验收分别记录。接入步骤见 [Linphone SIP 实验室](docs/integrations/linphone-sip-lab.md#在-ai-与渠道接入中管理模型组合)。

千问 Token Plan 独立 LLM 测试入口：在 backend 中执行 `python -m app.sip_lab_qwen_probe probe --acknowledged`，本机不回显输入套餐Key，默认仅一次 Flash 合成请求；`--compare` 对照 Flash/Max，`--suite` 校验数字、日期、否定和结束意图。按操作者授权用于本人非商业测试，无实际Key仍待实测，不接电话或租户活动。现有完整云语音探针继续使用按量付费 API；详见 [个人版模型验证](docs/integrations/linphone-sip-lab.md#千问-token-plan-个人版交互式模型验证2026-10-09)。


本地模型联调新增管理员只读宿主状态、检查时间和 Mac 启动前 `doctor`。沿用现有页面布局与刷新入口；诊断不加载模型或中断电话。详见 [SIP/模型接入指南](docs/integrations/linphone-sip-lab.md#宿主当前状态与-mac-启动前检查2026-10-08)。
