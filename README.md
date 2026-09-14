# 履衡 AI · FulfillOps Cloud 全栈开发版

当前版本：`v0.8.0`。本版本新增受控 DeepSeek/OpenAI-compatible 模型 Gateway、严格出网与单次费用门禁，以及显式批准的真实 Provider 回放模式。外部调用默认关闭；数据库成员关系、确定性业务服务和持久作业仍分别承担授权、裁决与恢复。

已选方向：第 1 版浅色 SaaS 工作空间 + 第 3 版 Agent 运行详情，支持全局深浅主题。

## 使用

完整开发环境可从项目根目录执行 `docker compose up --build`，访问 `http://localhost:4177`；API 文档位于 `http://localhost:8000/api/docs`。

前端采用 React 19 + Vite + Phosphor Icons。单独开发：`npm install`、`npm run dev`；测试：`npm test`；构建：`npm run build`。后端的环境、运行和测试方式见 `backend/README.md`。

## 核心体验路径

1. 清收活动 → 创建活动 → 目标与资产包 → 执行策略 → 确认启动模拟。
2. 点击「履约补款跟进」 → 查看运行、暂停/恢复 → 推进模拟 → 等待回款。
3. 模拟补款到账 → 确认 C002 的 ¥1,016 补款 → 查看本期已足额与新增 ¥152.40 应计佣金。
4. 案件侧栏可查看协议、六期还款计划、回款明细和运行依据。
5. 通过回款与佣金页查看净回款、计佣回款、应计佣金、实际收佣与 CSV 导出。
6. 在策略编辑中保存新版本；已有活动保留创建时的策略配置。
7. 使用全局「履衡 AI」查询案件、策略、任务、运营指标与钱指标；包含执行意图的问题先生成可审阅的结构化草案。
8. 在单个活动内通过服务端持久会话解释等待条件、重规划或发起暂停/恢复提案；确认时服务端重新校验成员角色与活动状态，再同步更新运行状态和审计证据。
9. 在「AI 与渠道」分别配置 Hermes、DeepSeek Harness、LangGraph 或自研 Agent Runtime，以及模型、语音与 SIP 电话服务，完成单项连接测试、五项全链路沙箱自测和管理员启用确认。
10. DeepSeek Harness 采用 `fulfillops-safe` Patch：对话结果展示 Provider Session、Turn、事件 Cursor、已核验工具数，以及“进程内续接/检查点重放”的真实恢复方式。
11. Compose 环境由独立 Worker 消费持久作业；页面显示 Worker 健康、排队数量和活动作业。Worker 异常退出后，过期租约会在尝试预算内自动重新排队。
12. Compose 使用 PostgreSQL 通知低延迟唤醒 Worker，通知不可用时自动保留数据库轮询；AI 与渠道页同步显示 Broker 与密钥信封状态。
13. `local-envelope` 使用租户/服务绑定的 AES-256-GCM 密文保存凭证，轮换后旧版本退役；主密钥不写入数据库。
14. 生产可切换到 AWS Secrets Manager：应用数据库只保存租户/服务绑定的 `aws-sm://` 引用，云端载荷再次校验租户与服务。
15. 企业 OIDC 固定使用显式非对称算法白名单、issuer、audience、JWKS 与必需声明；可追加 IdP 租户声明，但角色仍由数据库成员关系决定。
16. 管理员可运行 `fulfillops-safe-core` 契约回放；部署明确放行后也可运行一次真实模型安全评估，仅发送脱敏断言摘要并持久化摘要、Token 与保守费用。
17. 在异常中心处理异议、停止联系、金额冲突、授权缺失和委托到期；委托后尾期只核对被动到账，禁止主动触达。
18. 切换组织以查看租户隔离；组织设置中可还原整个演示。

支持活动搜索、按名称排序、紧凑行高、分页、批量暂停/恢复、资产包筛选、案件搜索、侧边栏快速搜索（⌘/Ctrl+K）、通知中心、键盘关闭弹窗与深浅主题。

## 数据与范围

这是可联调的全栈开发版，使用此前生成的 AMC 虚构样本。API 连接时，AI 与渠道配置、连接测试、自测报告、启用状态、活动快照和审计事件按租户入库；无 API 时才使用浏览器脱敏缓存。任何配置变更都会撤销旧报告与启用。

Hermes、语音和电话 Provider 当前仍返回确定性的沙箱适配器结果，不执行真实外呼、支付、邮件或生产系统写入。模型默认使用 `contract-only`；只有部署开关、出网白名单、允许模型、可解析密钥、连接测试和管理员确认同时满足时才可调用真实 Provider。DeepSeek Harness 的 `sandbox-contract` 不启动官方 SDK；`python-sdk` 已具备真实进程、MCP 工具与检查点恢复路径。`credential` 可由 `local-envelope` 加密入库，或由可选的 AWS Secrets Manager 托管；API 只返回末四位。`reference-only` 仍是默认零密钥回退。

企业认证通过 `AUTH_MODE=oidc` 显式启用。生产必须同时配置 `OIDC_ISSUER`、`OIDC_AUDIENCE` 与 `OIDC_JWKS_URL`，并关闭开发头身份与开发令牌。`GET /api/v1/security/auth/health` 只返回校验策略是否就绪，不暴露 issuer、audience 或 JWKS 地址。

租户 A 初始确认净回款 ¥19,920、计佣回款 ¥18,420、应计佣金 ¥2,778、实际收佣 ¥0。C002 模拟补款后分别为 ¥20,936、¥19,436、¥2,930.40、¥0；C002 第二期完成，后续分期仍未到期。租户 B 始终为 8 个案件、净回款 ¥800、应计佣金 ¥160。

样本触达时段和金额策略来自演示配置，不代表正式委托授权。异议、停止联系、资料问题和委托到期等保护状态不能用普通「恢复」按钮解除。

## 视觉与验证

使用原稿中提取的品牌资产；图标使用 Phosphor 图标库。经营首页进一步对齐 BoardUI 的侧边栏搜索、紧凑工具栏、柔和卡片、带坐标轴图表与高密度数据表，并保留 AI 经营建议、保护暂停和可追溯 Agent 运行语义。「AI 与渠道」页面由四张入口卡承载 Agent Runtime、模型、语音和电话，并增加接入进度、五项自测、启用门禁和最近报告。`design-qa.md` 记录浏览器验证、视觉对比与已知限制，`qa/` 保存截图证据。

本项目保留 Vite 与 Sites 兼容构建。`v0.8.0` 的 CI 基线为 55 项后端、13 项前端领域/API 契约和 4 项站点构建测试，共 72 项；新增覆盖模型 SSRF/重定向阻断、允许模型、JSON 空响应、429 脱敏、Token/预算、显式确认、真实回放零原文持久化、SQLite 兼容升级，以及 v0.4 到当前版本的 PostgreSQL 原地升级。后续仍需在正式云账号、企业 IdP 和真实 DeepSeek 凭证下做环境验收，并完成支付回执和佣金账簿。

重新导出独立 HTML：先执行 `npm run build`，再运行 `python3 scripts/export-standalone.py`，结果位于 `export/LuhengAI_FulfillOps_Interactive.html`。
