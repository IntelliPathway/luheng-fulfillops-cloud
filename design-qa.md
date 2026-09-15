# Design QA · AI 与渠道及 AI Native 工作台

# v0.16.0 server-authoritative catalog · 2026-09-15

- Asset and case screens now expose a visible “server authoritative / offline sandbox” source label.
- Dedicated server pagination replaces client-side slicing in connected mode; filters show loading, empty and fail-closed error states.
- Removed invented online debt age, contact time, debt-component and simulated-contribution values; missing facts render as unavailable.
- Online policy detail is read-only until the governed policy-write API exists; imported draft packages cannot start an activity.
- Vite production compilation and HTTP/API contract checks validate the layout and state paths. The cloud browser restriction remains, so no new screenshot evidence is claimed.

# v0.15.0 governed asset import · 2026-09-15

- Replaced the static sample-only import dialog with a server-backed CSV preview and maker-checker commit workspace.
- Reused the approved modal, metrics, status badge, warning and compact list patterns; no new visual language was introduced.
- Added explicit offline-preview, PII rejection, source-digest, draft-policy and non-overwrite messaging.
- Vite production compilation and frontend/backend contract tests validate the interaction structure. The cloud browser remains unable to access the local preview, so this pass does not claim new screenshot evidence.

# v0.14.0 brand and documentation system · 2026-09-15

- Replaced the generic diamond mark with an original AI neural-route/control-loop mark.
- Updated current UI terminology to 履约智控 AI / RepayGuard AI and 履约活动.
- Reorganized README and product specification around diagrams, matrices, state machines and acceptance gates.
- Vite production build and local HTTP asset/title checks are authoritative for this pass.
- The cloud browser blocked the local preview with `ERR_BLOCKED_BY_CLIENT`; no new screenshot or visual-runtime pass is claimed.

## v0.13.0 production governance and documentation · 2026-09-15

- 本版没有改变主工作台的视觉方向；新增的启动阻断页只在认证失败或可达 API 配置错误时出现，明确区分企业身份、API 故障和离线演示。
- 生产构建不再注入开发操作者身份；Sites 的结构化 `mode=sites-demo` 仍能进入明确标注的在线交互沙箱。
- Vite 将 React、Phosphor 图标和业务应用拆为独立包，最大业务包低于 500 KB，生产构建无大包警告。
- README 从版本功能堆叠重构为开机入口；产品功能、架构、配置、运维、安全和贡献规范分别形成稳定文档。
- 本版没有进行新的页面视觉调整或生成新截图；既有 `qa/` 证据继续对应 v0.12 视觉基线。

## v0.12.0 governed repayment plans · 2026-09-15

- 新增「履约计划」一级工作台，使用现有 BoardUI 卡片、筛选标签、紧凑表格与状态色，展示待复核、生效、完成、逾期、临近到期、期次进度和证据摘要。
- 签约提案弹窗只提交外部协议引用和签约/存证系统返回的 SHA-256 摘要；独立复核弹窗展示当前资产包规则、提案人、版本、证据摘要和完整期次，批准按钮受管理员、非提案人和明确确认共同约束。
- 案件侧栏优先读取服务端方案与期次，展示实际分摊、剩余金额、逾期状态和委托期后尾期，不再依赖前端硬编码的六期样本。
- 生产构建、20 项前端领域/API 契约和 Vite 同源代理运行验收通过；代理入口返回页面标题、API `0.12.0`、4 个生效方案、1 个待复核方案和共 6 个租户内方案。
- 云端浏览器访问 `http://127.0.0.1:4177` 仍被客户端策略以 `ERR_BLOCKED_BY_CLIENT` 拦截，因此本版不声称新增截图；既有 BoardUI 视觉基线保持不变，新增页面由构建、契约和真实 HTTP 渲染验证覆盖。

## v0.11.0 protection workflow and ECS packaging · 2026-09-15

- 异常中心已从静态演示数据切换为服务端保护事件概览，展示优先级、责任人、SLA、来源事件、证据版本和永久保护边界。
- 打开、补证、独立复核、版本冲突、停止联系不可解除和“批准后不自动恢复”均有后端测试与 HTTP 冒烟覆盖。
- Vite 生产构建和前端契约测试通过；本轮云端浏览器对本机 `127.0.0.1` 与 `terminal.local` 均返回 `ERR_BLOCKED_BY_CLIENT`，因此没有把不可达状态误报为视觉验收通过。
- ECS/1Panel 交付包含同源 Nginx 代理、安全响应头、仅回环入口、私有 API/PostgreSQL、资源上限、日志轮转和只读容量检测。

- Source visual truth: `/workspace/scratch/f1577a7a0ff3/upload/d40a9b90-d596-450f-be07-20d83ca9394b.png`
- Browser-rendered implementation: `/workspace/scratch/f1577a7a0ff3/recovery-cloud/qa/03-integrations-revised.png`
- Focused implementation region: `/workspace/scratch/f1577a7a0ff3/recovery-cloud/qa/04-integrations-grid-revised.png`
- Side-by-side comparison: `/workspace/scratch/f1577a7a0ff3/recovery-cloud/qa/integrations-comparison.png`
- Viewport: `1348 × 926` CSS px, device density `1`
- Source pixels: `2048 × 413`; normalized source: `1020 × 234`
- Implementation pixels: full `1348 × 926`; focused region `1020 × 234`
- State: 租户 A、浅色运营工作区、演示沙箱、Agent 与模型连接通过、语音与电话等待测试

## Full-view comparison evidence

参考图的核心视觉语言已保留：白底、轻灰服务卡、蓝色线性图标与主操作、克制圆角和高密度 SaaS 信息结构。实现增加左侧租户导航、全局 AI 查询、连接进度、沙箱自测和启用门禁，以承载真实产品的信息架构。四张服务卡比参考图三张更密集，是加入 Agent Runtime 后的有意变化。

## Focused-region comparison evidence

同高归一化对比显示，服务卡在标题、说明、分割线和底部动作上的层级与参考一致。修订后 `Agent Runtime` 不再换行，正文和辅助信息字号提升，四卡仍能在目标视口同屏。图标全部使用 Phosphor，不使用自绘 SVG 或占位资源。无需额外局部放大；卡片标题、状态、正文和操作在聚焦截图中均可辨认。

## Findings and iteration history

### Iteration 1

- [P1] 渠道启用后，对话任务草案仍提示“只能创建纯模拟活动”。
  - Fix: 文案与执行模式统一读取当前租户 `integrationState.enabled`。
  - Post-fix evidence: 浏览器验证草案的正文与“执行模式”一致；启用后显示授权渠道，未启用时显示纯模拟。
- [P1] 委托到期后的分期期次只在 C002 特例中展示，C014 无法看到五个委托后尾期。
  - Fix: 按真实 `installment_schedules` 与 `mandate_end` 比较，所有已签方案统一显示尾期数量、禁止主动触达和逐期授权边界。
  - Post-fix evidence: C014 协议页显示 5 个委托期后尾期，第 2—6 期均标注“禁止主动触达”。
- [P2] 四张服务卡在 1348px 视口标题拥挤、正文和辅助文字偏小。
  - Fix: 收紧标题区间距，标题固定 16px 单行，正文提升到 12px，关键辅助文本提升到 10px。
  - Post-fix evidence: `qa/03-integrations-revised.png` 与 `qa/04-integrations-grid-revised.png`。
- [P2] 对话确认“暂停”仅出现提示，没有更新活动状态和审计事件。
  - Fix: 确认草案后调用活动状态更新，并记录带 Run/Step/Tool 的事件；恢复同样重新校验并留痕。
  - Post-fix evidence: 浏览器中 Agent 状态从“运行中”更新为“已暂停”，正文显示新动作暂停语义。

### Final pass

- Fonts and typography: 中文系统无衬线层级清楚；核心正文和配置辅助文本已达到本原型可读阈值。长英文 Agent 名称不再折行。
- Spacing and layout rhythm: 服务卡、进度、自测和门禁形成稳定的 16px 节奏；密度高但无控件遮挡。
- Colors and tokens: 蓝色主操作、浅灰表面、绿色通过和琥珀等待语义一致；状态同时有文字和图标。
- Image and asset fidelity: 参考图无产品图片；实现使用矢量图标和真实品牌字标资源，没有低清占位图或 CSS 伪造图标。
- Copy and content: 配置、连接测试、自测与启用严格分开；Agent、模型、语音、电话四类能力的动作口径一致。
- Interaction: 已测试四类服务配置、单项连接测试、五项沙箱自测、管理员启用、全局 AI 查询、对话任务草案、活动预检、Agent 指令确认、工具轨迹、异常保护、尾期规则和回款证据链。
- Console: 干净标签页无应用来源 error/warn；仅浏览器扩展自身元数据错误，与应用无关。

## Residual test gaps

- 截图不能证明完整键盘顺序、屏幕阅读器播报和真实供应商延迟；这些需在生产前做可访问性与集成测试。
- 当前为前端演示，不连接真实模型、语音、SIP、支付或 AMC 账务系统。

final result: passed

## v0.3 runtime verification · 2026-09-14

- UI keeps the approved BoardUI layout; the integration page adds one compact Agent Gateway strip, persistent job identifiers, and database-derived identity/role labels without changing the primary card hierarchy.
- Frontend domain/API/Sites checks: 15 passed; production build emitted the required client, worker and hosting files.
- Backend checks: 14 passed, covering membership-derived RBAC, development Bearer JWT, tenant isolation, idempotent async jobs, Agent ChatBI sources/tool trace, and structured pause confirmation.
- HTTP smoke: page title, development identity, Bearer identity, Hermes sandbox Gateway, persistent Agent job and money-metric answer all returned successfully.
- Cloud browser access to loopback and `terminal.local` was blocked by the browser client in this runtime, so v0.3 did not replace the existing visual screenshots. The unchanged layout remains covered by the earlier screenshot evidence; new runtime surfaces were verified through build and HTTP rendering.
- Residual production gaps: enterprise IdP/JWKS, real KMS, external Hermes/LLM/ASR/TTS/SIP adapters, dedicated worker broker, payment callback and commission ledger remain intentionally unconnected.

## v0.3.1 DeepSeek Harness verification · 2026-09-14

- The Agent configuration modal now exposes DeepSeek Harness, transport, `fulfillops-safe` profile, persistence mode and a developer-preview warning.
- Server tests verify one Provider Session survives multiple turns, Turn/Cursor advance monotonically, runtime checkpoints stay tenant-scoped, and shell/direct-ledger requests are blocked before business execution.
- Selecting `python-sdk` without the official SDK and an approved FulfillOps safety plugin produces a failed connection job; the UI cannot present a contract-only test as a real provider connection.
- Activity creation, Agent detail and usage surfaces now resolve the configured Runtime instead of presenting Hermes as the only implementation.
- Vite production build and the Sites worker suite pass. The cloud browser client still blocks loopback URLs with `ERR_BLOCKED_BY_CLIENT`, so visual verification used compile/render checks and HTTP smoke rather than replacing screenshot evidence.

## v0.4 DeepSeek Harness SDK/MCP verification · 2026-09-14

- Updated the existing integration and Agent runtime status surfaces without changing the approved BoardUI layout or information hierarchy.
- The Runtime selector now distinguishes the zero-provider contract sandbox from the real SDK/JSON-RPC path; the Agent answer status distinguishes in-process continuation from database-checkpoint replay and shows verified tool count.
- `npm test` passed 12 domain/API-contract tests and 4 Sites packaging tests; `npm run build` produced the required client, server and hosting artifacts.
- Backend coverage passed 23 tests across MCP discovery/call framing, scoped Runtime JWT, tenant isolation, SDK job projection, process reuse, restart replay and failed-run persistence.
- No real DeepSeek credential was available in this environment, so this QA cycle intentionally made no provider call. The approved page structure did not change; existing screenshot evidence remains the visual baseline.

## v0.5 durable Worker verification · 2026-09-14

- The integration header now distinguishes API connectivity from job execution health: inline development, healthy external Worker, or degraded external Worker with jobs retained in the database.
- The sidebar status uses the same queue source and does not imply that a queued job is executing when no Worker heartbeat is available.
- Existing compact BoardUI density, light/dark Agent themes and responsive layout were preserved; no structural visual redesign was introduced.
- Automated verification covers external enqueueing, concurrent claim exclusivity, lease heartbeat, stale recovery, bounded attempts, lease fencing and cooperative cancellation. Backend 30, frontend/API 12 and Sites 4 checks passed; both the production build and standalone export completed successfully.
- The cloud browser again rejected the loopback preview with `ERR_BLOCKED_BY_CLIENT`. The UI delta is limited to queue-health text and status color, so the approved screenshots remain the visual baseline; current output was verified through production compilation, HTTP rendering and both inline/external-process smoke tests.

## v0.6 secret and broker verification · 2026-09-14

- The integrations header adds compact Broker and secret-store status without changing the approved card grid, hierarchy or responsive behavior.
- API output exposes only backend readiness, key version and secret count. References, nonces, ciphertext and plaintext never enter the frontend state.
- Local tests cover AES-GCM persistence, tenant/service binding, credential retirement, previous-key rotation and transient Runtime injection. GitHub Actions passed the PostgreSQL 17 migration/notification path and the complete 55-test release suite.
- `npm test` and the production build pass. The cloud browser loopback restriction remains unchanged, so the existing screenshot baseline is retained for this text-only status delta.

## v0.7 production assurance verification · 2026-09-14

- The integrations page adds one compact three-card assurance row for enterprise identity, tenant secret storage and the latest Agent replay. It reuses existing card, pill and button tokens and collapses to one column below 980px.
- The replay button is admin-only and disabled when the API is offline. A completed run refreshes the persisted pass count and dataset digest; the copy explicitly states that no external model is called.
- API and UI contracts expose only OIDC policy readiness, secret backend/key class and replay digests. Issuer/JWKS URLs, secret references, credential values and raw prompts do not enter the assurance cards.
- Backend verification passed 47 local tests with the PostgreSQL service test skipped; frontend/API passed 13 tests and Sites passed 4. The v0.7 smoke completed all four replay cases with zero external model calls and no activity state change.
- GitHub Actions run `34876163686` passed the PostgreSQL 17 migration path and the complete 65-test v0.7 release baseline for commit `a4161a3`.
- The local API and Vite server both started successfully. The cloud browser again rejected `http://127.0.0.1:4177` with `ERR_BLOCKED_BY_CLIENT`, so no new screenshot is claimed; production compilation, rendered asset inspection, API smoke and the existing BoardUI screenshot baseline cover this incremental row.

## v0.8 governed model gateway verification · 2026-09-14

- The integrations page extends the assurance row from three to four cards and adds a compact replay-mode selector. It reuses the existing card, pill, select and button tokens; the responsive breakpoint still collapses the grid to one column below 980px.
- The model form now separates `contract-only` from `live-provider`, exposes output/cost ceilings and explains the deployment-level egress gate. A native confirmation is required immediately before a real Provider replay, and the option remains disabled until the Gateway reports every gate ready.
- Backend verification passed 54 local tests with the PostgreSQL service test skipped; frontend/API passed 13 and Sites passed 4. The v0.8 smoke completed the four deterministic cases with zero external calls and confirmed that an acknowledged live request still returns HTTP 409 while the deployment switch is off.
- GitHub Actions run `34879324462` passed the PostgreSQL 17 migration path and the complete 72-test v0.8 release baseline for commit `474d855`.
- A combined runtime smoke loaded the application shell through Vite, proxied `/api/v1/health` to API `0.8.0`, and returned model Gateway state `contract`. The API and Vite development servers remain started on ports 8000 and 4177.
- The cloud browser rejected both `terminal.local:4177` and direct loopback access with `ERR_BLOCKED_BY_CLIENT` / local-URL policy. No new browser screenshot is claimed; production compilation, live HTTP proxy verification and the approved BoardUI screenshot baseline cover this incremental controls-only change.
- No real DeepSeek credential was available and `ENABLE_LIVE_MODEL_CALLS` stayed false, so this QA cycle made no Provider request and incurred no model cost.

## v0.9 financial ledger verification · 2026-09-14

- The payments workspace now treats server-returned integer-cent ledgers as authoritative when the API is connected. Five separate metrics distinguish confirmed recovery, commission-eligible recovery, accrued commission, confirmed settlement and collected commission.
- The receipt modal states the HMAC, provider-event idempotency and immutable-ledger boundary. Its submit action is disabled when the API reports that the sandbox or resolvable signing secret is unavailable; no payment signing secret is serialized by the frontend.
- Resetting the interactive demo no longer changes server money facts. It resets local workflow state and refreshes the tenant ledger from the API.
- Backend verification passed 63 local tests with one PostgreSQL service test skipped; frontend/API passed 15 and Sites passed 4. The financial smoke accepted one signed receipt, deduplicated its replay, rejected a bad signature with HTTP 401 and rejected collection beyond confirmed settlement with HTTP 409.
- The layout change is limited to an additional money metric, evidence copy and settlement event table. Production compilation and live HTTP verification cover the new data path; browser-loopback screenshot status is recorded during final runtime verification.
- A combined runtime check loaded `/payments` through Vite, proxied `/api/v1/health` as API `0.9.0`, and returned the seeded `sandbox-amc` ledger totals from the same port. The local API and Vite development processes were also started on ports 8000 and 4177.
- The cloud browser again rejected `terminal.local:4177` and `127.0.0.1:4177` with `ERR_BLOCKED_BY_CLIENT`. No new browser screenshot is claimed; the existing approved visual baseline, production compile and live proxy check cover this incremental financial-data change.

## v0.9.1 Sites deployment verification · 2026-09-15

- Hosted-domain detection now labels the product as a Sites interactive demo whenever the business API is unavailable. Agent, integration and payment surfaces consistently state that actions stay in browser state and do not update the server ledger.
- The Sites Worker returns an explicit JSON 503 for `/api/*`, preserves SPA deep-link fallback only for safe HTML GET/HEAD requests, and attaches CSP, HSTS, nosniff, frame, referrer and browser-permission protections.
- Frontend/API checks pass 15 cases; Sites Worker/build checks pass 5 cases. The production build emits the required client, Worker and hosting manifest artifacts.
- This deployment is intended for private product walkthroughs. It does not claim that FastAPI, PostgreSQL, payment providers or model/voice/telephony services are hosted by Sites.

## v0.10.0 receipt reconciliation verification · 2026-09-15

- 回款与佣金页新增「回执复核」标签、待复核计数、双人复核原则、候选案件、责任人和最近复核记录，保持原有 BoardUI 紧凑表格密度。
- 复核弹窗区分“创建提案”和“独立复核”两种状态；批准按钮只有在管理员、非提案人、明确勾选确认三项同时满足时可用。
- 桌面端对账表采用横向滚动保护，窄屏下提案摘要从四列回流为两列，复核历史摘要保持可读。
- 离线演示预置一条脱敏待复核回执；批准或驳回均明确标注不会写入服务端账簿。
- 生产构建和前端领域测试通过。受当前云浏览器本机地址策略限制，`terminal.local` 与 `127.0.0.1` 均被浏览器客户端拦截，因此本版未新增截图，也未把该限制误判为应用错误。
