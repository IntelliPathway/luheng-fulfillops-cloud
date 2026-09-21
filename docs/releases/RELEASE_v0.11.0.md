# 履衡 AI FulfillOps Cloud v0.11.0

## 本次迭代

- 新增租户隔离的保护事件模型、PostgreSQL 增量迁移与服务端 API。
- 保护事件打开即阻断案件与相关活动，并保留来源摘要、责任人、SLA 和审计事实。
- 解除采用“证据回执 → 提案 → 独立管理员复核”的 Maker–Checker 流程。
- 版本冲突、自审、缺少证据、委托续期证据不足和停止联系解除均 fail closed。
- 批准后案件只进入“待重新评估”，不会自动恢复既有活动；存在其他保护事件时继续阻断。
- 异常中心与处置弹窗改为读取服务端保护事实，展示证据摘要、复核版本和永久保护边界。
- 新增 ECS/1Panel 生产式 Compose、Nginx 同源 API 反向代理、安全响应头、资源上限和容量预检脚本。

## ECS 基线

- 最低验收：2 vCPU / 4 GB / 60 GB SSD，建议配置 2 GB Swap。
- 推荐：4 vCPU / 8 GB / 100 GB SSD / 5 Mbps 以上。
- 该配置只运行 Web、API、Worker 和 PostgreSQL，模型调用走远程服务，不包含本地大模型推理。
- 真实部署前必须在目标 ECS 执行 `scripts/ecs-capacity-check.sh`；外网验收档需要 1Panel 访问密码或 IP 白名单，生产档必须切换企业 OIDC。

## 安全边界

- 仅 Web 绑定宿主机回环端口；API 与 PostgreSQL 不发布宿主机端口。
- 默认关闭真实模型调用、官方 Harness Runtime 和支付沙箱。
- 密钥只从部署环境注入，不进入 Git、浏览器状态、API 响应或审计明文。
- Docker 日志轮转、容器内存/CPU 上限、只读应用文件系统和 no-new-privileges 已启用。
