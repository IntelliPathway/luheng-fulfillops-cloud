# 履约智控 AI · RepayGuard AI 发布说明

当前稳定版本：**v3.3.0**（2026-09-23）

本文件是发布说明的固定入口，只展示当前版本。逐版本验收证据统一归档到 [发布历史](docs/releases/README.md)，避免版本文件持续堆积在仓库根目录。

## 本次目标

建立 Phone、SMS、Email Provider 的受控试点认证流程。

## 主要变化

- 三渠道统一采用配置、契约测试和独立批准状态机。
- 真实模式强制 HTTPS、外部密钥引用和部署白名单开关。
- AI 与渠道工作台展示 Provider 模式、版本和认证证据状态。

## 验收结果

- maker-checker、自审阻断、版本校验和租户隔离通过。
- 未启用部署开关时真实 Provider 测试失败关闭。

## 进一步阅读

- [v3.3.0 完整发布证据](docs/releases/RELEASE_v3.3.0.md)
- [v1.5 进展评估](docs/v1.5-progress-assessment.md)
- [人工确认备忘录](docs/manual-confirmation-memo.md)
- [完整变更记录](CHANGELOG.md)
