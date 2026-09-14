# 履衡 AI · FulfillOps Cloud v0.8.0

## 本版目标

在 v0.7 的密钥、身份和确定性回放基础上增加受控模型出网能力，使 DeepSeek/OpenAI-compatible Provider 可以被真实验证，但默认保持零外部调用，且不改变清收状态机、保护规则、账务和人工审批的最终裁决权。

## 已完成

- 新增模型 Gateway，支持 OpenAI-compatible Chat Completions JSON Object 请求；DeepSeek 默认 Endpoint 为 `https://api.deepseek.com`。
- 新增 `contract-only` 与 `live-provider` 两种执行模式；保存配置、契约测试和真实连接测试保持独立。
- 真实调用同时要求部署开关、HTTPS 出网白名单、允许模型、可解析租户密钥、当前配置连接通过和管理员确认。
- 默认拒绝重定向、URL 内嵌凭证、查询参数、本地/私有地址及非标准端口；企业私网必须由部署方显式放行。
- 每次请求限制输入长度、超时、最大输出 Token 和保守费用上限；Provider 429/5xx 被归一为可恢复错误，不持久化原始错误正文。
- JSON 空内容、非对象或异常响应直接失败，避免把 Provider 的“成功 HTTP”误报为业务验收通过。
- `live-provider` 回放只发送合成/脱敏后的断言与工具状态摘要，不发送案件详情、通话文本或账务数据。
- 回放持久化数据集/请求/响应摘要、Token 用量、保守费用、模型配置版本和 Provider 请求 ID 哈希；不保存提示词、模型原文或凭证。
- 新增模型网关健康接口和前端“模型出网”状态；真实模型回放选项仅在全部门禁就绪时开放，并再次弹出费用/数据确认。
- 新增 PostgreSQL `007_governed_model_gateway.sql`，同时为既有 SQLite 开发库提供幂等的增量列升级。

## 验证口径

- 后端：本地 54 项通过、1 项 PostgreSQL 专项按环境跳过；CI 应执行完整 55 项。
- 前端领域/API：13 项；Sites：4 项。
- 发布基线：共 72 项自动化测试及生产构建。
- 单测使用 `httpx.MockTransport` 与内存模型结果，覆盖 SSRF、未授权模型、429 脱敏、空 JSON、预算、部署开关、显式确认和零原文持久化。
- v0.8 冒烟在 `ENABLE_LIVE_MODEL_CALLS=false` 下完成契约回放，并确认真实调用请求以 HTTP 409 失败关闭。

## 仍需部署验收

- 当前环境没有真实 DeepSeek 凭证，因此本次开发和测试没有向模型 Provider 发起请求或产生费用。
- 正式环境需配置网络出口、DNS/代理策略、密钥 Provider、允许模型、费用上限和监控告警后，再执行一次人工批准的 `live-provider` 回放。
- DeepSeek 模型名、价格与并发限制会变化；部署前需按官方文档复核允许列表和预算参数。
- AWS IAM/KMS、企业 IdP、支付回执、佣金账簿、ASR/TTS/SIP 生产线路仍需环境级验收或后续开发。

参考：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[错误码](https://api-docs.deepseek.com/quick_start/error_codes)。
