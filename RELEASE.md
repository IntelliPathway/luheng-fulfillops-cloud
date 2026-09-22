# 履约智控 AI · RepayGuard AI 发布说明

当前稳定版本：**v2.2.0**（2026-09-21）

本文件是发布说明的固定入口，只展示当前版本。逐版本验收证据统一归档到 [发布历史](docs/releases/README.md)，避免版本文件持续堆积在仓库根目录。

## 本次目标

建立多渠道通信沙箱与统一合规门禁。

## 主要变化

- Phone、SMS、Email 使用同一联系任务和跨渠道频次门禁。
- SMS/Email 默认仅进入沙箱队列，不向外部发送消息。
- AI 指挥台展示每个渠道的 Provider、sandbox 或 disabled 状态。

## 验收结果

- 覆盖沙箱渠道创建、跨渠道频次阻断和渠道就绪契约。
- 电话 Provider 未就绪时继续失败关闭。

## 进一步阅读

- [v2.2.0 完整发布证据](docs/releases/RELEASE_v2.2.0.md)
- [v1.5 进展评估](docs/v1.5-progress-assessment.md)
- [人工确认备忘录](docs/manual-confirmation-memo.md)
- [完整变更记录](CHANGELOG.md)
