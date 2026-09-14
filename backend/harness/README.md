# DeepSeek Harness 接入边界

`fulfillops-safe.contract.json` 是履衡 AI 对 Agent Runtime 的安全契约，不是已经部署完成的 DeepSeek Harness 插件。

- `sandbox-contract`：验证租户隔离、工具白名单、行动提案、会话检查点和恢复语义，不启动外部进程、不调用真实模型。
- `python-sdk`：预留给官方 Python SDK/JSON-RPC。只有安装 SDK、设置 `FULFILLOPS_DSH_PLUGIN_READY=1`，并完成专用插件验收后才允许进入连接测试；当前仍主动返回“生产驱动尚未启用”。
- 禁止使用官方 `sdk-minimal` 的默认 shell 直接接触案件或生产网络。业务插件只能调用 FulfillOps 受控 API，不能获得数据库、账务、文件系统或通用 HTTP 权限。

生产插件验收至少覆盖：C002 案件查询、钱指标口径查询、ACT-001 暂停提案、进程重启后的会话恢复、跨租户拒绝和全部禁用工具拒绝。
