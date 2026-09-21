# 履衡 AI · FulfillOps Cloud v0.5.0

## 本版目标

把异步作业从 API 请求进程中的后台回调升级为可独立部署、可并发扩展、可在进程崩溃后恢复的持久 Worker，同时保留本地零依赖开发模式。

## 已完成

- 新增数据库队列租约：队列、优先级、可用时间、租约所有者、租约到期、心跳、恢复次数和取消请求全部持久化。
- 新增独立 `python -m app.worker` 进程；Compose 默认让 API 只入队，由 Worker 消费。
- PostgreSQL 使用 `FOR UPDATE SKIP LOCKED`，SQLite 使用带状态条件的原子更新；并发 Worker 不会取得同一作业。
- 长作业定期续租任务与 Worker 心跳；API 可查看当前租户排队、运行和陈旧作业及活跃 Worker 数。
- Worker 异常后，过期租约在尝试预算内自动重新排队；达到上限则失败，遗留 AgentRun 会明确关闭并保存原因。
- 终态写入使用租约所有者与尝试次数 fencing；失去租约的旧 Worker 不能覆盖新所有者的状态。
- 排队作业可立即取消；运行作业采用协作式取消，在当前处理器安全边界完成后进入取消终态。
- 新增 PostgreSQL 事务迁移执行器及 `004_durable_worker_queue.sql`，支持从现有 v0.4 数据卷原地升级。
- 前端显示 API v0.5、内联/外部执行模式、Worker 健康和排队作业数。

## 验证结果

- 后端：30 项测试通过。
- 前端领域与 API 契约：12 项测试通过。
- Sites 构建适配：4 项测试通过。
- 合计：46 项自动化测试通过，生产构建通过。
- 真实 CLI 进程冒烟覆盖 API 入队、独立 Worker 认领、作业完成和租约清理。
- 同步 HTTP 冒烟覆盖身份、Gateway、Agent 作业、经营指标回答与 Runtime 检查点。

## 仍需部署验收

- 当前执行后端是 PostgreSQL 数据库队列，尚未接入 Redis Streams、RabbitMQ 或 Kafka；后续可在现有认领契约后增加 Broker Adapter。
- 本次运行环境没有 Docker/PostgreSQL 可执行文件，因此已校验 Compose 配置与 PostgreSQL 迁移语义，但容器化数据库升级仍需在部署环境验收。
- 当前环境未注入真实 DeepSeek API Key，因此不会产生模型调用或费用；真实模型回放仍属于部署验收。
- 正式 KMS/Secret Manager、企业 IdP、支付回执、佣金账簿和 ASR/TTS/SIP 仍待接入。
