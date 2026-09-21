# 履衡 AI · FulfillOps Cloud v0.6.0

## 本版目标

把 v0.5 的独立 Worker 推进到可部署的生产基础：凭证可加密持久化、Worker 可被 PostgreSQL 事务通知低延迟唤醒，并由持续集成验证旧数据卷升级。

## 已完成

- 新增 `local-envelope` 密钥后端：AES-256-GCM、96 位随机 nonce、租户/服务/引用 AAD 绑定和版本化主密钥。
- 更新凭证会原子写入新信封并退役旧信封；`SECRET_PREVIOUS_KEYS` 支持主密钥轮换窗口。
- 新增 `python -m app.secret_cli rotate`，可按全部租户或单租户把存量活动信封重包裹到当前主密钥版本。
- 模型密钥仅在 Agent Runtime 启动前瞬时解密到进程内存，不写入配置、任务、日志、审计或响应。
- 保留 `reference-only` 默认模式，未配置主密钥时不会把伪引用误报为可解密密钥。
- 新增管理员密钥健康接口和前端密钥信封状态，接口不暴露引用或密文。
- 新增 `postgres-notify` Broker：API 的 `pg_notify` 与作业入队使用同一事务，提交后唤醒 Worker。
- PostgreSQL 通知只是唤醒信号，作业事实、并发认领、租约和恢复仍完全由数据库队列保证。
- 新增 `005_secret_envelopes.sql`，现有 v0.4/v0.5 PostgreSQL 数据卷可原地升级。
- 新增 GitHub Actions：Python 3.12、Node 22、PostgreSQL 17，执行后端 lint/测试、v0.4 升级、通知 Worker、前端测试与生产构建。

## 验证口径

- 本地：38 项后端通过，PostgreSQL 专项 1 项按环境跳过。
- CI：PostgreSQL 17 环境执行 39 项后端测试，包含 v0.4 原地升级、事务通知与 Worker 消费，全部通过。
- 前端领域/API：12 项；Sites：4 项。
- 完整 CI：55 项自动化测试及生产构建全部通过。

## 仍需部署验收

- `local-envelope` 是自托管信封实现；正式生产建议把主密钥托管到云 KMS/HSM，并增加对应 Provider。
- 当前运行环境未提供本地 PostgreSQL/Docker；PostgreSQL 原地升级与通知链路已由 GitHub Actions 服务容器验收通过。
- 尚未注入真实 DeepSeek API Key，不会产生模型调用或费用。
- 企业 IdP、支付回执、佣金账簿和 ASR/TTS/SIP 生产线路仍待接入。
