# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Approved direction
Product name is 履衡 AI, with the English descriptor FulfillOps Cloud. Use 履衡 AI for the assistant and product brand; the former 澄清 / Recovery Cloud name is retired.

Combine the latest displayed option 1 light SaaS workspace with option 3 persistent Agent execution. Use the exact source brand extraction and Phosphor regular icons. Offer a dark theme for the Agent experience. Build a frontend-only Chinese demo using the AMC sample kit, tenant-scoped state, signed-plan/payment distinctions, and no external calling or payment execution. User explicitly requested a complete interactive prototype, including supporting screens needed for that journey.

The latest visual reference is a compact BoardUI-style desktop SaaS dashboard: rounded left navigation, restrained light-gray cards, blue primary actions, dense metrics and a full-width operational table. Apply that visual logic to AI collection objects rather than copying generic finance content. The home screen should prioritize AI runs, confirmed recovery, accrued commission, automation quality, exceptions, and case-level next actions.

Further BoardUI polish should keep the interface calm and operational: place quick search in the sidebar, keep utility actions compact, use consistent soft-gray surfaces and 14–18px radii, give charts clear axes and period controls, and make table status/action affordances immediately scannable. Preserve the AI-native product layer through live execution summaries, explicit evidence, protected-pause semantics, and actionable notifications.

AI 与渠道接入必须作为独立的租户级能力：分别配置大模型服务、语音服务和电话接入，配置保存不等于可用；必须依次完成连接测试、沙箱全链路自测和管理员启用确认。任何配置变更都应使旧测试报告和启用状态失效。原型中的自测只能使用模拟回执与脱敏白名单号码，不发送密钥、不发起真实外呼。

The v0.2 backend baseline uses FastAPI + SQLAlchemy, SQLite for zero-dependency local tests, and PostgreSQL through DATABASE_URL for the composed development environment. Frontend API access must retain an explicit offline demo fallback. The server, not the browser, is authoritative for tenant scope, credential references, service versions, self-test validity, enablement and activity preflight. Never silently fall back to local writes after the API has been detected as connected.

The v0.3 backend baseline makes tenant membership authoritative for RBAC, supports Bearer JWT/OIDC with development auth explicitly gated, and persists connection tests, self-tests and Agent turns as recoverable jobs. Agent conversations, tool traces, evidence and action proposals live on the server; pause/resume proposals require structured confirmation and a fresh server-side permission/state check. Real provider calls remain behind adapters and must not be inferred from a successful sandbox contract test.

AI Native 采用“结构化运营台 + 全局对话入口 + 活动内类自主 Agent”的产品形态。支持 Hermes Agent、LangGraph 或自研 Runtime 经统一 Agent Gateway 接入；对话负责查询、解释和生成行动草案，确定性策略、状态机、账务与保护规则负责最终裁决。全局履衡 AI 必须能按租户查询案件、策略、任务、运行、运营指标和钱指标，并显示口径与来源。创建活动、触达、暂停/恢复、预算、策略发布、解除保护和账务变更不得由聊天直接执行；高影响操作先进入结构化确认。Agent、模型、语音和电话都需纳入服务快照、工具轨迹、失败恢复和五项沙箱自测。

DeepSeek Harness 作为 Hermes/LangGraph 的可选 Agent Runtime，而非业务内核替代品。当前开发预览版只允许通过 `fulfillops-safe` 适配契约进入系统：保留持久会话、事件游标和插件化 Runtime 优势，但禁用 shell、文件系统、任意 HTTP、直接账务写入、解除保护与策略发布。官方 SDK/JSON-RPC 只有在专用业务工具插件完成验收后才可启用；沙箱契约通过不得标记为真实 Provider 已接通。

The v0.4 Harness baseline implements that production boundary with the official Python SDK plus a `fulfillops-safe` patch and a dedicated MCP subprocess. The patch disables both sdk-minimal persistent shell variants and registers only eight scoped tools. Runtime tool grants are short-lived and bound to tenant, logical Agent session and scope. Reuse the same Provider Session only while its subprocess is alive; after process restart, replay the bounded database checkpoint into a new Provider Session and label that recovery mode explicitly. A real connection still requires deployment opt-in, the optional SDK package and KMS-injected model credentials.

The v0.5 job baseline separates API enqueueing from execution with a leased database queue and an independent Worker. PostgreSQL workers claim jobs with `SKIP LOCKED`; the SQLite development path uses a conditional claim. Every running job has an owner, lease expiry and heartbeat. Expired leases are recovered with an audit event and a bounded attempt budget, orphan Agent runs are closed as failed, and running-job cancellation is cooperative at the handler safety boundary. Keep `inline` mode only for zero-dependency local development and tests; composed environments use `external` mode.

The v0.6 infrastructure baseline adds tenant-bound AES-256-GCM secret envelopes and a PostgreSQL notification broker. The master key and previous rotation keys only enter through the deployment environment; plaintext credentials may exist transiently in process memory but never in settings, API responses, audit events or database columns. `reference-only` remains the zero-secret local fallback. PostgreSQL `LISTEN/NOTIFY` is a wake-up optimization only: the leased database queue stays authoritative and polling must recover lost notifications. GitHub Actions must validate a v0.4-to-current PostgreSQL migration before release.

The v0.7 production-access baseline adds an optional AWS Secrets Manager backend, fail-closed enterprise OIDC/JWKS validation and auditable deterministic Agent replay acceptance. AWS references and payloads are both tenant/service bound; credentials never enter application database columns or API responses. OIDC permits only configured asymmetric algorithms, requires issuer, audience and core temporal claims, and may enforce an IdP tenant claim without allowing token roles to override database membership. Replay suites run through the same safe Gateway contract, store immutable dataset/output digests and must remain isolated from business writes and external model calls unless a future live-provider mode is separately approved.
