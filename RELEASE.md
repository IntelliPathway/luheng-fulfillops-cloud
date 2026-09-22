# 履约智控 AI · RepayGuard AI 发布说明

当前稳定版本：**v2.1.0**（2026-09-21）

本文件是发布说明的固定入口，只展示当前版本。逐版本验收证据统一归档到 [发布历史](docs/releases/README.md)，避免版本文件持续堆积在仓库根目录。

## 本次目标

建立生产试点发布门禁，让自动证据与人工确认有统一验收入口。

## 主要变化

- 自动核验 PostgreSQL、OIDC、开发身份关闭、HTTPS/CORS、Runtime 密钥、演示种子和迁移模式。
- 试点验收页区分业务阻断、生产配置阻断和外部人工确认。
- CI 新增 CodeQL SAST 与 Trivy 容器高危漏洞扫描。

## 验收结果

- 开发环境发布门禁失败关闭测试通过。
- 前端验收台读取服务端发布门禁契约。
- 既有业务、安全和构建测试保持通过。

## 进一步阅读

- [v2.1.0 完整发布证据](docs/releases/RELEASE_v2.1.0.md)
- [v1.5 进展评估](docs/v1.5-progress-assessment.md)
- [人工确认备忘录](docs/manual-confirmation-memo.md)
- [完整变更记录](CHANGELOG.md)
