# 履衡 AI · FulfillOps Cloud v0.9.1

## 本版目标

把已验证的 v0.9 产品界面发布为可访问的 Sites 测试站点，同时明确静态交互沙箱与服务端生产能力的边界，避免线上演示被误认为已经连接 FastAPI、PostgreSQL、支付或模型 Provider。

## 已完成

- 注册并绑定履衡 AI 的 Sites 项目，构建产物包含前端静态资源、Cloudflare Workers-compatible 入口和托管清单。
- 托管域名自动显示“Sites 演示”和“API 未接入”；Agent、支付及回款文案同步使用在线交互沙箱口径。
- `/api/*` 在未配置业务后端时返回 JSON HTTP 503 和 `no-store`，不会被 SPA 回退误转换为成功页面。
- SPA 深层路由继续回退到 `index.html`，静态资源缺失和非 GET/HEAD 请求保持原始错误。
- Worker 为站点响应附加 CSP、HSTS、`nosniff`、同源 Frame、Referrer Policy，以及禁用摄像头、麦克风和定位的 Permissions Policy。
- Sites 演示中的支付、Agent 和配置操作仅影响当前浏览器状态，不写入 v0.9 服务端不可变账簿，也不发起外部调用。

## 验证口径

- 后端完整基线：64 项；本地 63 项通过、1 项 PostgreSQL 专项由 CI 执行。
- 前端领域/API 契约：15 项。
- Sites Worker 与构建安全：5 项。
- 发布基线共 84 项，并要求生成 `dist/client/index.html`、`dist/server/index.js` 和 `dist/.openai/hosting.json`。

## 部署边界

- 当前 Sites 版本是私有测试站点，保持仅所有者访问；未改变为公开或工作空间共享。
- FastAPI、PostgreSQL、OIDC、密钥后端、支付 webhook、DeepSeek Harness 和独立 Worker 仍由外部运行环境承载。
- 后续若要让 Sites 访问真实业务 API，应使用受控 HTTPS 后端、企业身份和明确的 CORS/隧道绑定，不能在前端保存任何 Provider 密钥。
