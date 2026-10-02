# 履约智控 AI · RepayGuard AI 发布说明

当前稳定版本：**v3.1.0**（2026-09-23）

本文件是发布说明的固定入口，只展示当前版本。逐版本验收证据统一归档到 [发布历史](docs/releases/README.md)，避免版本文件持续堆积在仓库根目录。

## 本次目标

把套餐权益和资源配额落实为服务端强制门禁。

## 主要变化

- Agent、模型回放、策略实验和生产渠道活动统一校验套餐能力。
- 席位、月度 Agent Run 与模型预算达到限额后失败关闭。
- 成员双人复核在落库前重新计算当前有效席位。

## 验收结果

- Pilot 套餐能力拒绝和席位边界测试通过。
- 平台、成员治理、策略实验与 Agent 主链回归通过。

## 进一步阅读

- [v3.1.0 完整发布证据](docs/releases/RELEASE_v3.1.0.md)
- [v1.5 进展评估](docs/v1.5-progress-assessment.md)
- [人工确认备忘录](docs/manual-confirmation-memo.md)
- [完整变更记录](CHANGELOG.md)
