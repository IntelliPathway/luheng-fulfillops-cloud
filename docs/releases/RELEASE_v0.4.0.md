# 履衡 AI · FulfillOps Cloud v0.4.0

## 本版目标

把 DeepSeek Harness 从“安全契约 PoC”升级为可运行、可关闭、可失败恢复的可选 Runtime，同时保持清收业务状态机、保护规则、账务与授权策略的确定性边界。

## 已完成

- 接入官方 `deepseek-harness-sdk` 的 `DeepSeekHarness.run()`/JSON-RPC/stdio 运行路径，并保留零外部调用的 `sandbox-contract`。
- 新增 `fulfillops-safe.patch.yml`：禁用 `sdk-minimal` 默认的持久 Bash/Pwsh，只挂载 FulfillOps MCP Client。
- 新增独立 MCP Server，精确发布 8 个只读/提案工具；未知工具和 7 类危险能力不进入目录。
- 新增内部受控工具 API。Runtime JWT 有独立 audience/issuer，绑定租户、Agent Session 与 scope，20 分钟过期；工具层再次校验案件/活动资源范围。
- 模型密钥只从 KMS/部署环境注入；SDK 缺失、未显式启用、Patch 缺失、模型路由不兼容或密钥缺失时连接测试明确失败。
- 解析 SDK `tool/call`、`tool/result` 和最终回答，持久化可核验工具轨迹、来源、证据、提案、事件数与完成原因；不保存模型私有推理。
- 进程存活时复用 Provider Session；进程重启后把有界脱敏数据库检查点重放到新 Provider Session，规避官方协议当前无 resume/open 的限制。
- Runtime 崩溃时保留失败的 `AgentRun`，支持作业重试，不因事务回滚丢失诊断记录。
- 前端明确显示契约沙箱/真实 SDK、进程内续接/检查点重放、Turn、Cursor 与已核验工具数。
- Docker 支持 `INSTALL_HARNESS=true` 可选构建，不让默认开发镜像承担 SDK 体积。

## 验证结果

- 后端：23 项测试通过。
- 前端领域与 API 契约：12 项测试通过。
- Sites 构建适配：4 项测试通过。
- 合计：39 项自动化测试通过，生产构建通过。
- 覆盖 MCP 初始化/工具目录/调用、Runtime JWT、跨租户隔离、完整 SDK Agent 作业、提案投影、进程内续接、进程重启检查点重放和失败 Run 持久化。

## 仍需部署验收

- 当前环境未注入真实 DeepSeek API Key，因此没有产生模型调用或费用；真实模型回放需在部署环境完成。
- 官方 DeepSeek Harness 仍是预稳定接口；本版固定 PyPI 已发布的 `0.1.5rc1`，升级前必须执行协议回归。
- 企业 IdP、正式 KMS/Secret Manager、独立 Worker/Broker、支付回执、佣金账簿、ASR/TTS/SIP 仍待接入。

官方资料：<https://github.com/deepseek-ai/deepseek-harness>、<https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md>、<https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sdk/protocol/README.md>。
