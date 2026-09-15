# 变更记录

本项目按语义版本维护。详细验收证据见各版本 `RELEASE_*.md`。

## [0.14.0] - 2026-09-15

### Added

- 中文产品名“履约智控 AI”、英文产品名“RepayGuard AI”及统一品类描述；
- 原创神经节点与受控闭环 Logo、浏览器图标和品牌使用规范；
- 品牌/版本集中常量与防漂移契约测试；
- README 结构图、产品主链图和文档视觉导航。

### Changed

- 当前 UI、API、CLI 与稳定文档统一使用新品牌；
- “清收活动”统一调整为更中性的“履约活动”；
- README 重构为开机入口，产品规格重构为能力地图、矩阵、状态机和验收表；
- 独立交互导出改为 `RepayGuardAI_Interactive.html`。

### Compatibility

- 仓库名、镜像名、环境变量前缀、Webhook 头和 `fulfillops-safe` 命名空间保持不变；
- 历史发布说明和迁移注释保留当时名称。

## [0.13.0] - 2026-09-15

### Added

- 生产启动配置模型与失败关闭校验；
- 显式数据库 bootstrap、演示种子开关和首租户身份初始化 CLI；
- 数据库就绪检查与独立存活检查；
- 完整产品、架构、配置、运维、安全和贡献文档；
- 一键工程验证脚本。

### Changed

- 1Panel 模板默认企业 OIDC，关闭开发身份、演示种子和 ORM 自动建表；
- API/Worker 镜像改为非 root 用户；
- 前端认证/API 错误不再静默降级为可写演示；
- 后端统一 Ruff 格式并纳入 CI；
- 前端拆分 React、图标与业务构建包；
- CI 执行真正的 ECS 部署契约测试。

### Security

- 生产构建不再自动发送 `X-Actor-ID: Terry`；
- 已有真实租户的数据库拒绝混入 AMC 演示种子。

## 历史版本

- [0.12.0](RELEASE_v0.12.0.md)：服务端签约与分期履约台账；
- [0.11.0](RELEASE_v0.11.0.md)：保护事件与异常处置、ECS/1Panel 栈；
- [0.10.0](RELEASE_v0.10.0.md)：Maker–Checker 回执对账；
- [0.9.0](RELEASE_v0.9.0.md)：验签回执与不可变财务账簿；
- [0.8.0](RELEASE_v0.8.0.md)：受控模型 Gateway；
- [0.7.0](RELEASE_v0.7.0.md)：OIDC、AWS Secrets Manager 与安全回放；
- [0.6.0](RELEASE_v0.6.0.md)：密钥信封与 PostgreSQL 通知；
- [0.5.0](RELEASE_v0.5.0.md)：持久作业和独立 Worker；
- [0.4.0](RELEASE_v0.4.0.md)：DeepSeek Harness 安全桥；
- [0.3.0](RELEASE_v0.3.0.md)：RBAC、Agent 会话与持久作业基础；
- [0.2.0 之前](RELEASE_v0.3.0.md)：原型与首个后端基线。
