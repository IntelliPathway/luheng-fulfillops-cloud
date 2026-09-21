# 履约智控 AI · RepayGuard AI 发布说明

当前稳定版本：**v1.9.0**（2026-09-21）

本文件是发布说明的固定入口，只展示当前版本。逐版本验收证据统一归档到 [发布历史](docs/releases/README.md)，避免版本文件持续堆积在仓库根目录。

## 本次目标

补齐可抓取指标、SLO、软件物料清单和依赖审查门禁。

## 主要变化

- 新增 Prometheus 0.0.4 文本指标和进程窗口 SLO 状态。
- 指标按路由模板聚合，资源编号不会成为指标标签。
- 构建可重复的 CycloneDX 1.5 前后端依赖清单并作为 CI 构件保存。
- Pull Request 对高危依赖变化执行审查。

## 验收结果

- 管理员 JSON 指标、SLO 与 Prometheus 指标契约通过。
- 路由模板聚合测试覆盖动态案件 URL。
- SBOM 同时包含 npm 与 PyPI 组件且不包含凭据。

## 进一步阅读

- [v1.9.0 完整发布证据](docs/releases/RELEASE_v1.9.0.md)
- [v1.5 进展评估](docs/v1.5-progress-assessment.md)
- [人工确认备忘录](docs/manual-confirmation-memo.md)
- [完整变更记录](CHANGELOG.md)
