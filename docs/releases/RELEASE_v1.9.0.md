# 履约智控 AI · RepayGuard AI v1.9.0

## 主题

可观测性、SBOM 与依赖门禁。

## 功能

- 管理员可读取 JSON 运行指标、进程窗口 SLO 和 Prometheus 文本指标。
- 请求指标按 FastAPI 路由模板归集，避免案件号等资源 ID 形成高基数标签。
- CI 生成并保存 CycloneDX 1.5 软件物料清单。
- Pull Request 中的高危依赖变化会阻断合并。

## 安全边界

- 运营指标不使用租户、案件或用户作为 Prometheus 标签。
- SBOM 只包含包名、版本与 purl，不读取环境变量或凭据。

## 验收

- SLO、Prometheus、动态路由归一化和 SBOM 双生态覆盖测试通过。
