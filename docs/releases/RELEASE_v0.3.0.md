# 履衡 AI · FulfillOps Cloud v0.3.0

## 本版目标

把 v0.2 的可联调全栈原型升级为“可信身份 + 持久 Agent + 可恢复作业”的生产接入基础，减少关键能力只存在于浏览器演示状态的问题。

## 已完成

- 用户、租户成员与 `viewer / operator / admin` 数据库权限；请求头不能自行提升角色。
- Bearer JWT/OIDC 验证接口和受环境开关控制的本地开发身份。
- 持久异步作业：排队、运行、成功、失败、取消、重试、幂等键和租户隔离。
- Agent Gateway 契约：Hermes Agent、LangGraph Runtime、自研 Runtime 使用同一服务入口。
- 服务端 Agent 会话、用户/助手消息、运行、工具轨迹、证据和来源。
- 服务端 ChatBI 演示查询：案件、活动、策略、确认净回款、计佣回款、应计佣金和实际收佣。
- 暂停/恢复只生成结构化提案；确认时重新校验租户成员角色、有效期和活动状态。
- 连接测试和五项沙箱自测改用持久作业；旧同步接口暂时兼容。
- 前端展示身份来源、Agent Gateway 状态、作业编号和服务端/离线运行模式。
- PostgreSQL 增量迁移 `002_identity_agent_jobs.sql`。

## 验证结果

- 后端：14 项测试通过。
- 前端领域与 API 契约：11 项测试通过。
- Sites 构建适配：4 项测试通过。
- HTTP 冒烟：身份、Bearer、Gateway、Agent 会话、异步作业和 ChatBI 回答通过。
- 合计：29 项自动化测试通过。

## 安全与业务边界

- 真实 Hermes、LLM、ASR/TTS、SIP、支付和 AMC 账务系统尚未连接。
- 当前 Provider 结果属于沙箱契约验证，不能解释为线路或供应商已真实开通。
- 生产必须关闭 `ALLOW_DEV_HEADER_AUTH` 与 `ALLOW_DEV_TOKEN`，配置企业 OIDC/JWKS，并替换正式 KMS。
- LLM/Agent 不拥有策略、保护状态或账务事实的最终裁决权。

## 下一版建议

优先实现独立 Worker/Broker、正式 KMS、Hermes Agent Gateway HTTP/SSE 适配器和 OpenAI-compatible 模型适配器；随后接入语音/SIP 沙箱与支付、佣金账簿。
