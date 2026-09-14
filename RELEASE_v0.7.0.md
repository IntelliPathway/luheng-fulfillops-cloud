# 履衡 AI · FulfillOps Cloud v0.7.0

## 本版目标

把 v0.6 的本地安全基础推进到可进行生产接入验收的边界：云端密钥引用、企业身份失败关闭，以及不调用真实模型的可审计 Agent 回放。

## 已完成

- 新增 `aws-secrets-manager` 密钥后端和独立可选依赖；使用工作负载角色、区域、路径前缀和可选客户 KMS Key。
- 应用数据库仅保存 `aws-sm://` 引用及凭证末四位；云端 JSON 载荷和引用路径均绑定租户与服务类型。
- 云端凭证更新使用 Secrets Manager 新版本；异常响应只落错误类型摘要，不记录 SDK 错误正文或凭证。
- 新增 `AUTH_MODE=oidc` 企业认证：issuer、audience、JWKS、非对称算法白名单、必需声明、缓存和时钟偏移均显式受控。
- 可选 `OIDC_TENANT_CLAIM` 对令牌允许的工作空间做第二层限制；角色继续由数据库成员关系解析。
- 新增管理员认证健康接口；返回校验策略和开发开关，不暴露 IdP URL。
- 新增 `fulfillops-safe-core` 回放套件与 `agent.model_replay` 持久作业，覆盖钱指标、保护状态、暂停提案和危险工具阻断。
- 回放保存数据集 SHA-256、逐项检查、工具轨迹、输出摘要、通过/失败计数和审计事件；重复幂等键不会创建第二次运行。
- 回放采用隔离的 `deterministic-contract`，不读取模型密钥、不调用外部模型、不确认提案、不改变活动状态。
- 新增 `006_oidc_aws_replay.sql`，GitHub Actions 继续从 v0.4 基线验证原地升级。
- 前端接入页增加企业身份、密钥 Provider 和最近安全回放三项生产验收状态。

## 验证口径

- 后端：本地 47 项通过、1 项 PostgreSQL 专项按环境跳过；[GitHub Actions #4](https://github.com/IntelliPathway/luheng-fulfillops-cloud/actions/runs/34876163686) 已在 PostgreSQL 17 下执行完整 48 项并通过。
- 前端领域/API：13 项；Sites：4 项。
- 发布基线：共 65 项自动化测试及生产构建。
- AWS 测试使用内存 Fake Client，不访问真实 AWS、不读取账号凭证、不产生费用。
- OIDC 测试使用临时 RSA 密钥与 Fake JWKS Client，覆盖错误 audience、缺失 `iat`、HS256 算法混淆、租户越权，以及自定义开发密钥下的本地 issuer/audience 一致性。

## 仍需部署验收

- 正式 AWS 账号中的 IAM 最小权限、KMS Key Policy、CloudTrail、Secret 生命周期和跨区灾备尚未验证。
- 正式企业 IdP 的发现文档、JWKS 轮换、注销/撤权时效和应急回退尚未验证。
- 当前回放不是“真实 DeepSeek 输出回放”；真实 Provider 模式仍需脱敏数据集、模型版本固定、费用预算和人工评分门禁。
- 支付回执、佣金账簿、ASR/TTS/SIP 生产线路仍未接入。
