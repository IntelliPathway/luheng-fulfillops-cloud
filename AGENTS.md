# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Approved direction
Product name is 履约智控 AI, with the English name RepayGuard AI and the descriptor AI 智能催收与回款管理平台. Use these names in current product UI and stable documentation. 履衡 AI, FulfillOps Cloud, 澄清 and Recovery Cloud are retired customer-facing names; old names may remain only in release history, repository identifiers and compatibility-sensitive integration paths.

Combine the latest displayed option 1 light SaaS workspace with option 3 persistent Agent execution. Use the approved original RepayGuard AI neural-route mark and Phosphor regular icons. Offer a dark theme for the Agent experience. Build a frontend-only Chinese demo using the AMC sample kit, tenant-scoped state, signed-plan/payment distinctions, and no external calling or payment execution. User explicitly requested a complete interactive prototype, including supporting screens needed for that journey.

The latest visual reference is a compact BoardUI-style desktop SaaS dashboard: rounded left navigation, restrained light-gray cards, blue primary actions, dense metrics and a full-width operational table. Apply that visual logic to AI collection objects rather than copying generic finance content. The home screen should prioritize AI runs, confirmed recovery, accrued commission, automation quality, exceptions, and case-level next actions.

Further BoardUI polish should keep the interface calm and operational: place quick search in the sidebar, keep utility actions compact, use consistent soft-gray surfaces and 14–18px radii, give charts clear axes and period controls, and make table status/action affordances immediately scannable. Preserve the AI-native product layer through live execution summaries, explicit evidence, protected-pause semantics, and actionable notifications.

AI 与渠道接入必须作为独立的租户级能力：分别配置大模型服务、语音服务和电话接入，配置保存不等于可用；必须依次完成连接测试、沙箱全链路自测和管理员启用确认。任何配置变更都应使旧测试报告和启用状态失效。原型中的自测只能使用模拟回执与脱敏白名单号码，不发送密钥、不发起真实外呼。

The v0.2 backend baseline uses FastAPI + SQLAlchemy, SQLite for zero-dependency local tests, and PostgreSQL through DATABASE_URL for the composed development environment. Frontend API access must retain an explicit offline demo fallback. The server, not the browser, is authoritative for tenant scope, credential references, service versions, self-test validity, enablement and activity preflight. Never silently fall back to local writes after the API has been detected as connected.

The v0.3 backend baseline makes tenant membership authoritative for RBAC, supports Bearer JWT/OIDC with development auth explicitly gated, and persists connection tests, self-tests and Agent turns as recoverable jobs. Agent conversations, tool traces, evidence and action proposals live on the server; pause/resume proposals require structured confirmation and a fresh server-side permission/state check. Real provider calls remain behind adapters and must not be inferred from a successful sandbox contract test.

AI Native 采用“结构化运营台 + 全局对话入口 + 活动内类自主 Agent”的产品形态。支持 Hermes Agent、LangGraph 或自研 Runtime 经统一 Agent Gateway 接入；对话负责查询、解释和生成行动草案，确定性策略、状态机、账务与保护规则负责最终裁决。全局履约智控 AI 必须能按租户查询案件、策略、任务、运行、运营指标和钱指标，并显示口径与来源。创建活动、触达、暂停/恢复、预算、策略发布、解除保护和账务变更不得由聊天直接执行；高影响操作先进入结构化确认。Agent、模型、语音和电话都需纳入服务快照、工具轨迹、失败恢复和五项沙箱自测。

DeepSeek Harness 作为 Hermes/LangGraph 的可选 Agent Runtime，而非业务内核替代品。当前开发预览版只允许通过 `fulfillops-safe` 适配契约进入系统：保留持久会话、事件游标和插件化 Runtime 优势，但禁用 shell、文件系统、任意 HTTP、直接账务写入、解除保护与策略发布。官方 SDK/JSON-RPC 只有在专用业务工具插件完成验收后才可启用；沙箱契约通过不得标记为真实 Provider 已接通。

The v0.4 Harness baseline implements that production boundary with the official Python SDK plus a `fulfillops-safe` patch and a dedicated MCP subprocess. The patch disables both sdk-minimal persistent shell variants and registers only eight scoped tools. Runtime tool grants are short-lived and bound to tenant, logical Agent session and scope. Reuse the same Provider Session only while its subprocess is alive; after process restart, replay the bounded database checkpoint into a new Provider Session and label that recovery mode explicitly. A real connection still requires deployment opt-in, the optional SDK package and KMS-injected model credentials.

The v0.5 job baseline separates API enqueueing from execution with a leased database queue and an independent Worker. PostgreSQL workers claim jobs with `SKIP LOCKED`; the SQLite development path uses a conditional claim. Every running job has an owner, lease expiry and heartbeat. Expired leases are recovered with an audit event and a bounded attempt budget, orphan Agent runs are closed as failed, and running-job cancellation is cooperative at the handler safety boundary. Keep `inline` mode only for zero-dependency local development and tests; composed environments use `external` mode.

The v0.6 infrastructure baseline adds tenant-bound AES-256-GCM secret envelopes and a PostgreSQL notification broker. The master key and previous rotation keys only enter through the deployment environment; plaintext credentials may exist transiently in process memory but never in settings, API responses, audit events or database columns. `reference-only` remains the zero-secret local fallback. PostgreSQL `LISTEN/NOTIFY` is a wake-up optimization only: the leased database queue stays authoritative and polling must recover lost notifications. GitHub Actions must validate a v0.4-to-current PostgreSQL migration before release.

The v0.7 production-access baseline adds an optional AWS Secrets Manager backend, fail-closed enterprise OIDC/JWKS validation and auditable deterministic Agent replay acceptance. AWS references and payloads are both tenant/service bound; credentials never enter application database columns or API responses. OIDC permits only configured asymmetric algorithms, requires issuer, audience and core temporal claims, and may enforce an IdP tenant claim without allowing token roles to override database membership. Replay suites run through the same safe Gateway contract, store immutable dataset/output digests and must remain isolated from business writes and external model calls unless a future live-provider mode is separately approved.

The v0.8 model-access baseline adds a governed DeepSeek/OpenAI-compatible JSON gateway and an explicitly approved live-provider replay mode. Live calls require the deployment switch, HTTPS egress allowlist, allowed model, resolvable tenant credential, current connection test and an admin acknowledgement. Redirects, private/local destinations and unbounded output/cost are denied by default. Live replay may send only synthetic or redacted assertion/tool summaries; persist digests, token usage, conservative cost and hashed provider request IDs, never raw prompts, model text or credentials.

The v0.9 financial-integrity baseline treats signed payment receipts and immutable recovery/commission ledger entries as the only authoritative money source. Store money as integer cents, verify provider HMAC signatures against the exact raw body, enforce tenant-scoped event idempotency, and retain unmatched or disputed receipts outside business metrics. Refunds must reference an original payment and reuse its eligibility/rate. Settlement may not exceed accrued commission, and collection may not exceed confirmed settlement. Browsers and Agents must never receive payment signing secrets or write financial ledgers directly.

The v0.9.1 Sites baseline is an interactive frontend sandbox unless a separate business API is explicitly connected. Hosted pages must label that mode clearly. The Sites Worker must return structured 503 responses for `/api/*`, never rewrite APIs or non-GET requests to the SPA shell, and attach CSP, HSTS, nosniff, frame, referrer and browser-permission protections. A Sites deployment must never imply that FastAPI, PostgreSQL, payment webhooks or provider credentials are hosted with the static demo.

The v0.10 payment-reconciliation baseline isolates unmatched and disputed receipts from all money metrics. Candidate cases are deterministic, tenant-scoped suggestions captured with an evidence digest; they never authorize a ledger write. An operator or admin may create a proposal, but only a different admin account may approve or reject it. Approval revalidates the current receipt and case financial profile under a row lock and commits the recovery ledger, commission ledger, receipt state, review state, metrics and audit evidence atomically. Self-review, stale versions and the legacy one-step match path fail closed by default.

The v0.11 protection-incident baseline makes exception handling a server-owned workflow rather than frontend-only state. Opening a protection incident immediately blocks the case and related active activities. Resolution requires evidence references, an operator/admin proposal and review by a different admin under optimistic version control. Approval only moves an otherwise clear case to “待重新评估” and never resumes an old activity. Permanent stop-contact protection has no generic release path. The ECS/1Panel deployment remains remote-model-only, binds the web ingress to localhost for 1Panel reverse proxy, keeps API/PostgreSQL private, and defaults real models, Harness and payment sandbox off.

The v0.12 repayment-plan baseline makes signed agreements, installments and payment allocations server-owned facts. Proposals store external agreement references and cryptographic digests, never contract text, and must satisfy the current asset-package settlement, down-payment, installment-count and mandate policies. A different admin approves under optimistic version control and fresh policy validation. Signed receipts allocate oldest installment first; refunds reverse only their original allocations in newest-installment order. Protected cases, self-review, stale versions and direct Agent/accounting writes fail closed.

The v0.13 production-governance baseline requires an explicit production profile: PostgreSQL migrations only, no ORM schema creation, no demo seed, enterprise OIDC, HTTPS CORS, and no development header/token authentication. Production containers run as a non-root user. Authentication or reachable API failures must block browser writes rather than silently fall back to demo state; only an actual network failure or the explicit Sites demo contract may use the offline adapter. The first production tenant/member is provisioned idempotently through the dedicated identity CLI and never through AMC demo data.

The v0.14 brand-system baseline renames the customer-facing product to 履约智控 AI / RepayGuard AI and uses 资产回款运营平台 as the category descriptor. Brand strings and the release version are centralized in frontend/backend constants, and tests must prevent current UI and stable docs from drifting back to retired names. The original neural-route logo communicates AI reasoning, governed loops and measurable progress. README and product documentation should prefer diagrams, matrices, state machines and current screenshots over long prose. Compatibility-sensitive identifiers such as the repository slug, environment prefixes, webhook headers and fulfillops-safe tool namespace remain unchanged until a separately tested migration exists.

The v0.15 asset-import baseline makes CSV ingestion a governed server-owned workflow. Only the versioned non-PII schema is accepted; raw CSV content must never be persisted. Preview batches store a source digest, normalized safe rows and structured issues. Existing cases are skipped without overwrite, invalid rows or unsafe headers block the batch, and the preview creator may not confirm the same batch. A different admin commits against a fresh version, and packages, commission rules, cases, financial profiles, batch state and audit evidence are atomic. Newly imported packages remain draft until separately governed policy publication.

The v0.16 asset-catalog baseline makes online asset and case views server authoritative. Package and case lists, facets, filters, sorting, pagination, data-completeness scores, protection/plan summaries and money aggregates come from tenant-scoped APIs. Missing debt age, contact history or balance components stay explicitly unavailable and must never be reconstructed from frontend samples. An authenticated/reachable API failure fails closed; only the explicit offline/Sites demo mode may read `src/data`. Online policy surfaces remain read-only until a governed server write workflow exists, and a committed import must become visible through the catalog immediately.

The v4.2 pilot baseline supports public Sites runtime API/OIDC parameters via `/runtime-config.json`, with explicit demo mode when unset. No secrets enter this endpoint. Configured-backend failures block offline writes. The Sites Worker remains frontend-only and returns 503 for local `/api/*`; it never proxies arbitrary destinations. Production pilot preflight is read-only and does not grant external execution permissions. Mandate eligibility uses a single UTC date across catalog and activity preflight.

## v4.3 review and real case validation
Keep the BoardUI dashboard composition, readable supporting labels, undistorted original logo, sticky table action column, keyboard tabs, and responsive dialogs. Connected mode must never show fixed demo trends, quality scores, or sync times as real results. Case validation reads tenant-scoped imported cases and signed receipt/ledger provenance only; a source digest proves traceability, not authenticity. Always require external attestation before calling a case real-business verified.

## v4.4 connection diagnostics
Production connection readiness is a read-only technical gate, separate from business acceptance. Require a production profile, the current OIDC session, explicitly allowed HTTPS request origin, PostgreSQL migrations, healthy external Worker, independent admins and active tenant. Never treat a request Origin header alone as proof of browser CORS or external execution permission. Failed diagnostics clear stale results and exports; local/development or demo runtime cannot claim a production-ready Site.

## v4.5 external amount comparison
Case comparison accepts only an optional nonnegative integer-cent net amount for all periods. Treat it as an operator claim, never as external attestation. Missing or invalid receipt evidence must make comparison unavailable with a null difference. Changing case, amount, tenant or connection state clears prior exports.

## v4.6 recovery trends
Online recovery trends use tenant-scoped immutable recovery ledger entries, grouped by provider event time in UTC. Period totals are separate from all-time summaries. Preserve negative refunds and actual zero days. On request failure or scope change, clear stale results and never substitute demo charts.

## v4.7 daily ledger provenance
Daily detail shares the trend's tenant and provider-event UTC date basis. Pagination never changes whole-day totals. Receipt evidence is linked only with same-tenant, same-case, verified matched receipts and correct ledger backlinks. Clear stale detail on date/period/tenant changes and errors. Platform provenance is never external business attestation. Preserve BoardUI layout and modal keyboard/mobile usability.

## v4.8 daily reconciliation
Compare daily payment and refund amounts independently, using nonnegative integer cents and whole-day receipt provenance across all pages. Net equality cannot hide offsetting differences. Missing evidence makes differences null. Editing either amount invalidates comparison and export until a new request completes. Summary exports omit paginated items and never attest real business or authorize execution.

## v4.9 evidence gap triage
Daily evidence filters affect only the paginated detail rows and filtered count. Whole-day money, receipt completeness and comparison never narrow to the selected subset. SQL null links must remain incomplete. Report the first provenance gap without exposing other-tenant receipts. Reset pagination and clear stale results on filter changes; late responses cannot overwrite newer selections.


## v4.10 customer materials
Customer evidence is separate from governed asset ingestion. Encrypt originals with tenant/digest/mapping authenticated data, never persist plaintext, and fail closed on unavailable keys or corrupted originals. CSV amounts are strict nonnegative integer cents for all-time case payment/refund totals. Uploaded documents are operator claims, never verified signed receipts or external acceptance. Preserve BoardUI and keep offline uploads disabled.


## v4.11 inbound customer sync
Standard API/Webhook material events use existing tenant-scoped enterprise authentication, not payment-signature authentication. Persist encrypted event bodies and enqueue only opaque event references. Recheck the submitter’s current permissions in leased workers. Event idempotency never permits changed content; retries reuse the encrypted source and never write financial ledgers.


## v4.12 evidence association review
Suggestions use exact existing case identifiers, never AI guesses. CSV associations require the case to appear in authenticated source claims. Reviewers must differ from both material submitter and proposer. Bind decisions to current material/case evidence digests and use versioned conditional updates. Preserve decision references in audit history; approval never changes ledger or receipt facts.


## v4.13 case acceptance
Record external acceptance references/digests with independent administrator review. Bind each decision to fresh material/case/association/configuration evidence, external statement metadata and expiry. Acceptance requires separate payment/refund matches plus source, mandate, policy, protection, receipt and production gates. Configuration/evidence changes invalidate exports as current acceptance. Human attestation remains explicitly distinct from independently proven real-business authenticity and never grants external execution.


## v4.14 customer feed connectors
Customer connectors use exact deployment HTTPS host allowlists, validated public DNS addresses pinned for each TLS request, no redirects or proxies, encrypted Bearer secrets and three strict integer-cent mappings. Current configuration test and admin enablement precede pulls; configuration changes and failed retests revoke readiness. Workers recheck current administrator permissions. Persist encrypted idempotent events before cursor advancement; partial pages replay without financial ledger writes. Scheduled pulls reuse leased jobs and block bypassing retryable failed pages.


## v4.15 immutable versions and evidence assistance
Append material versions atomically, preserve originals, prevent branches and cross-series adoption, and bind reviews to the latest version state. Show historical evidence explicitly. Evidence workspace is tenant/case scoped; current source citations and digest govern suggestions and tasks. Rule mode is never labelled a model success. Model mode requires explicit permission to share sanitized checks, existing tested tenant model configuration and Gateway controls; never send originals, case IDs, source references or amounts. Reject invented citations and nonconforming output. Task recording cannot alter facts or approve acceptance; stale evidence invalidates decisions and exports. Preserve BoardUI mobile and error states.

## Functional availability acceptance
The user requires a complete audit of feature availability, not only visible layout. An unconfigured API may block authoritative writes but must not hide an otherwise useful page. Show available queries and evidence, explicit dependency/role reasons and a working onboarding path. Never display fixed demo metrics, tool steps, costs, test pass counts or stale evidence as online results. Parameter validation is not scenario replay. Agent proposal confirmation must not issue a second business mutation. Cover offline, role restrictions, partial failures, late responses, repeated submissions and mobile controls with meaningful browser regressions.

## v5.0 integration release
User requires one v5.0 delivery without further small versions. Preserve BoardUI and all v4.x capabilities. Unified acceptance reads fresh current tenant evidence, excludes stale/expired case approvals and cannot authorize external actions. Software CI and synthetic recovery are distinct from production customer acceptance. Test the exact Sites build and old deployed backend compatibility on isolated restored PostgreSQL databases.

## Enterprise onboarding continuation
Keep v5.0 as the unified release. Real OIDC workspaces must come from active database memberships intersected with optional tenant claims. Enterprise bootstrap is atomic, requires two distinct admins, cannot revive disabled identities or authorize business actions. Self-hosted identity templates and generated public deployment bundles are separate from actually running infrastructure. Maintain docs/enterprise-onboarding.md and report real test/deployment limits.

## Standard personal-loan MVP

User chose continued development in this existing project. Build one standard unsecured personal-loan collection slice; reuse identity, cases, queue and ledger. Current loan module is a deterministic sandbox, never real media or model execution. PTP is separate from a signed plan and a payment. Preserve opaque contact references, scoped admission, atomic cadence, idempotent events and stale-state checks. Real channel requests fail closed until Provider dispatch/media and institutional identity verification are actually implemented and accepted. Do not grow Harness selection, multi-channel or UI redesign during this MVP. Update stable docs and the single MVP plan instead of creating per-round release files.

## Iteration delivery authorization

User explicitly requires every iteration to update relevant documentation and push to the existing GitHub repository without repeated confirmation. Continue within the established MVP scope; report verification and deployment limits accurately. Sandbox dialogue grants expire after 30 minutes and recheck the authorizing administrator before new events; do not renew expired grants through idempotent retries.

Tenant standard-loan sandbox policy must be explicitly configured by an administrator. Enforce Asia/Shanghai same-day windows, conservative bounded limits, policy expiry and pause on admission and each new dialogue event. Freeze session policy snapshots, invalidate on revision, and keep tenant-before-case lock ordering for policy mutations and dialogue execution. Pending PTP/claimed payments remain holds; policy revisions do not reset daily counts. Policy is administrator assertion and never real-provider authorization.

Linphone is approved as a SIP test terminal. Use the isolated Asterisk echo lab before media/model integration. Lab calls use only fixed PJSIP/1001 and no customer facts or PSTN trunk. Persist dispatch intent before network I/O; unknown results or restarts must query the same channel ID without blind redial. A successful HTTP call or SIP endpoint state never proves RTP, AI dialogue, identity verification or real-channel acceptance. Live channel contract hashes must not mark delivery as verified; keep missing adapters fail closed. Update the lab guide and stable MVP documents with actual test limits.

The user's local model host is a Mac Studio M3 Ultra with 512 GB unified memory. Keep MLX model execution native and optional, separate from the production Linux backend. Fixed lab extension 1003 may connect the existing phone media bridge to local ASR/LLM/TTS; preserve 1002 echo verification. Use bounded internal test dialogue, explicit local credentials, revision-pinned model preparation and cancellation. Software or synthetic probe success must not claim Mac GPU, phone audio, performance or business acceptance without actual verification.

User requires quick model replacement for verification. Preserve single-stage comparison profiles, reviewed model aliases, data-only JSON configuration and separate ASR/LLM/TTS adapter contracts. Keep per-configuration pinned manifests and report the actual service selection/revisions; reject a probe against an old configuration or revision. Unimplemented native adapters must remain explicitly unavailable, never silently use a different model. Switch only after the old call and model process exit; preserve SIP credentials, resource journal and all business gates.


## Administrator voice combinations and activity use
User approved the product positioning AI 智能催收与回款管理平台 and the core page name AI 智能催收工作台. Keep the existing brand and compact BoardUI.
AI 与渠道接入 owns tenant model combinations, connection checks, single-stage comparisons and measured latency reports. Only admins configure, compare and enable; operators select an approved combination in an activity and run fixed synthetic validation without case facts or dialing. Freeze the actual config digest, revisions, config version and approval report in the activity. Any edit, failed retest, expiry or revoked authority invalidates current use; preserve historical evidence.
The optional Mac loopback model host manages only its own child, cached reviewed models and fixed internal probes. Never take over an unmanaged service; drain must reject active phone/model sessions. Wait for old process exit before starting a different configuration. No browser credentials, arbitrary provider URLs, automatic downloads, production loopback calls or inference retries after unknown results. Synthetic timing includes worker/stream queue waits and excludes weight loading, end-to-end HTTP round trips and phone playback; never label it real phone latency.


User chose to keep the current product UI after the UX review. Preserve existing navigation, layout and interaction; focus subsequent iterations on local backend/Mac model integration and independently verified phone media. Read-only model diagnostics must never drain, load, infer, download or terminate processes, nor change stored approvals. Treat current host status, synthetic benchmark and actual phone/device acceptance as distinct evidence. Keep private tokens and raw host responses out of browser diagnostics.

On 2026-10-09 the user explicitly authorized personal, noncommercial Token Plan model testing after being informed of the subscription usage restrictions. Provide a separate local CLI using the fixed Token Plan endpoint and reviewed Qwen3.8 aliases with bounded synthetic inputs, hidden credential entry and no automatic retries or billing-plan fallback. This experiment does not enable tenant activities, customer calling, phone media or production model use. Keep the pay-as-you-go voice probe's subscription-key mismatch guard.

The user-confirmed Token Plan Base URL is https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1, verified against the current Qianwen AI platform documentation. The dedicated LLM probe appends /chat/completions exactly once; keep the endpoint fixed and never use the URL as the API key.
