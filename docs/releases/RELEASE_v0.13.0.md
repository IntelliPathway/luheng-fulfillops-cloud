# 履衡 AI FulfillOps Cloud v0.13.0

发布日期：2026-09-15

## 发布主题

本版不增加新的高影响业务动作，重点把 v0.12 的功能基线收敛为更安全、可启动、可审计、可维护的交付候选。生产部署从“文档提醒关闭开发能力”升级为“代码和 Compose 强制失败关闭”。

## 代码与架构

- 新增 `StartupSettings`，统一校验环境、数据库、认证、CORS、种子、建表和 Runtime 密钥；
- 新增数据库 bootstrap 层，生产只运行版本化 PostgreSQL 迁移；
- 演示种子改为显式能力，真实租户数据库拒绝混入 AMC 样本；
- 新增幂等首租户/管理员初始化命令，只创建身份骨架和审计事件；
- API readiness 真实执行数据库查询，liveness 独立保留；
- 后端版本号集中到单一模块；
- API/Worker 容器以 UID `10001` 非 root 运行；
- Ruff format、import order 和常见 bug 规则进入 CI；
- Vite 拆分 React、Phosphor Icons 和业务包，消除单包超 500 KB 告警。

## 前端安全

- `X-Actor-ID: Terry` 只允许 Vite 开发模式或显式 `VITE_ENABLE_DEV_AUTH=true`；
- 401/403、服务端错误、网络断开和 Sites 明确演示 503 使用不同状态；
- 认证失败和 API 配置失败显示阻断页，不再进入可操作的浏览器回退；
- Sites 明确返回 `mode=sites-demo` 时继续使用只读/沙箱交互语义。

## ECS/1Panel

- 生产默认 `APP_ENV=production`、`AUTH_MODE=oidc`；
- 默认关闭开发身份、开发令牌、演示种子和 ORM 自动建表；
- OIDC issuer/audience/JWKS 和 HTTPS CORS 为 Compose 必填项；
- API 健康检查切换到 `/api/v1/health/ready`；
- 现有 `2C2G/40G` ECS 仍不满足最低部署门槛，本版未在该机器强行上线。

## 文档体系

- 根 README 重构为从克隆、开机、验证到停机/重置的入口文档；
- 新增详细产品功能规格、系统架构、完整配置参考和运维手册；
- 新增安全策略、贡献指南和统一变更记录；
- 明确区分当前实现、沙箱能力、生产前置条件和已知技术债。

## 验证基线

- 后端：90 项收集；本地 89 项通过，1 项 PostgreSQL 专项按环境跳过；
- 前端：21 项领域/API 契约；
- 部署：1 项 ECS/1Panel 安全契约；
- Sites：5 项构建/安全契约；
- 合计：117 项自动化测试，CI 使用 PostgreSQL 17 执行完整 90 项后端测试；
- Ruff lint 与 format check、Vite 生产构建、Compose 静态解析和历史业务冒烟纳入发布验证。

## 已知限制

- 通用浏览器 OIDC Authorization Code + PKCE 尚未内置；必须按最终 IdP 完成适配后再对外生产使用；
- `main.py` 与 `App.jsx` 仍偏大，本版先隔离启动/配置/初始化职责，后续继续领域拆分；
- 尚缺浏览器 E2E、覆盖率阈值、结构化日志/指标告警、SAST、容器扫描和 SBOM；
- 语音、电话和真实支付 Provider 仍需独立集成与合规验收。
