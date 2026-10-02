# 履约智控 AI · RepayGuard AI 发布说明

当前稳定版本：**v4.0.0**（2026-09-23）

本文件是发布说明的固定入口，只展示当前版本。逐版本验收证据统一归档到 [发布历史](docs/releases/README.md)，避免版本文件持续堆积在仓库根目录。

## 本次目标

交付企业级多租户生命周期与可信 AI 商业闭环。

## 主要变化

- Trial、Active、Grace、Suspended、Closed 状态受服务端状态机控制。
- 合同依据、部署区域、数据保留期与客户成功负责人进入治理记录。
- 生命周期变更采用 maker-checker，并与套餐权益强制联动。

## 验收结果

- 生命周期职责分离、版本冲突、租户隔离和权益暂停测试通过。
- v3.1 至 v4.0 主链回归、生产构建和发布检查通过。

## 进一步阅读

- [v4.0.0 完整发布证据](docs/releases/RELEASE_v4.0.0.md)
- [v1.5 进展评估](docs/v1.5-progress-assessment.md)
- [人工确认备忘录](docs/manual-confirmation-memo.md)
- [完整变更记录](CHANGELOG.md)
