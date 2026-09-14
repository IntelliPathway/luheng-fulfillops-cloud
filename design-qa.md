# Design QA · AI 与渠道及 AI Native 工作台

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
