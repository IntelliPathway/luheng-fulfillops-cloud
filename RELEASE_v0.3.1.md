# 履衡 AI · FulfillOps Cloud v0.3.1

## 本版目标

把 DeepSeek Harness 纳入可替换 Agent Runtime，同时维持长账龄清收业务的确定性边界：Agent 负责理解、查询、规划与提案；策略、状态机、保护规则和账务服务继续拥有最终裁决权。

## 已完成

- 新增 Provider-neutral Runtime Adapter，支持 Hermes、DeepSeek Harness、LangGraph 与自研 Runtime 快照。
- 新增 `fulfillops-safe` Harness 契约：8 个受控业务工具和 7 类明确禁用能力。
- 新增 `agent_runtime_checkpoints`，保存 Provider Session、Turn、事件 Cursor、最近 Run 和安全元数据。
- Agent 重复对话恢复同一检查点；UI 显示新建/恢复状态、Turn 与 Cursor。
- 新增 Runtime 状态 API，并继续执行用户、租户与角色隔离。
- DeepSeek Harness `sandbox-contract` 不启动外部进程；`python-sdk` 在 SDK/安全插件未就绪时明确失败。
- 配置页新增 Runtime、传输、Profile、安全策略、会话持久化和开发预览提示。
- 活动创建、Agent 详情和用量页按当前 Runtime 展示，移除关键路径中的 Hermes 硬编码。
- 新增 PostgreSQL 增量迁移 `003_deepseek_harness_runtime.sql`。

## 验证结果

- 后端：16 项测试通过。
- 前端领域与 API 契约：12 项测试通过。
- Sites 构建适配：4 项测试通过。
- 合计：32 项自动化测试通过。
- HTTP 冒烟覆盖开发身份、Bearer、Gateway、持久 Agent 作业、ChatBI 和 Runtime 检查点。

## 仍未完成

- 尚未安装或启动 DeepSeek Harness 官方 Python SDK。
- 尚未实现 DeepSeek Harness 的 FulfillOps 业务工具插件和真实模型回放。
- 真实 Hermes/LLM/ASR/TTS/SIP、KMS、支付回执与佣金账簿仍未连接。

DeepSeek Harness 当前处于开发预览阶段，生产升级需固定版本并执行兼容性回归。官方资料：<https://github.com/deepseek-ai/deepseek-harness>、<https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/python-sdk.md>。
