# Linphone SIP 联调

更新：2026-10-07。交付状态：联调工具、配置与模拟 HTTP 回归已完成；本环境没有 Docker/Asterisk/Linphone，未执行真实注册、构建、响铃或 RTP 测试。

## 用途与范围

Linphone 是 SIP 软电话，可使用自己的 SIP/PBX 账号。当前选择 Asterisk 测试网关，以 Linphone 分机 1001 验证通话链路。Linphone 拨 1000 可进行双向回声；本项目的主机 CLI 经 ARI 向固定分机 1001 发起测试，接听后进入同一个回声拨号计划。无 PSTN Trunk、客户手机号、案件数据、债务播报、录音或真实催收；真实模型和标准个贷 provider 模式保持阻断。

| 检查 | 可以证明 | 不能证明 |
|---|---|---|
| status 返回 endpoint online | 网关接口可查询、分机可用状态 | 实际音频正常或已完成机催 |
| call 返回 submitted | 网关确认指定呼叫 ID 的创建 | 已振铃、已接听或已送达客户 |
| inspect 返回 Ringing / Up | 当前通道状态 | RTP 双向音频、本人身份或 AI 对话可用 |
| Linphone 接听并听到自己的回声 | 人工检查当次音频往返 | 公网手机呼叫、ASR/TTS 或业务验收 |

CLI 始终返回 audio_verified=false、ai_dialogue_ready=false、pstn_enabled=false；不能靠配置或 HTTP 成功修改这些验收事实。主机实例与业务租户隔离，测试日志不写业务账本，不生成业务通话回执。

## 本机启动

需要已有 Docker Compose、Python 后端依赖和 Linphone。以下从仓库根目录执行。生成器随机创建独立 SIP/ARI 凭据与实例 ID，目录 0700、文件 0600，拒绝覆盖；generated 被 Git 忽略。请只在本机读取账号文件，不上传、粘贴到聊天或提交。

```bash
python scripts/sip-lab-config.py
docker compose --env-file deploy/sip-lab/generated/compose.env -f deploy/sip-lab/compose.yml up --build -d
```

在 Linphone 添加 SIP 账号，按 `deploy/sip-lab/generated/linphone-account.txt` 填写：默认身份 sip:1001@127.0.0.1、用户名 1001、UDP、PCMU/PCMA，密码从该文件读取。实验室没有 TLS/SRTP，适用同机或隔离 LAN；不要将该模板用于公网生产。

手机 Linphone 位于同一 LAN 时，在首次生成时设置运行 Docker 主机的实际私有 IPv4：

```bash
python scripts/sip-lab-config.py --host-address 192.168.1.20
```

示例地址须替换为主机真实地址。生成器不接受公网、0.0.0.0、IPv6 或组播地址；默认 SIP/RTP 只绑定本机。LAN 模式仅绑定指定私有地址，ARI 始终映射 127.0.0.1:8088。端口为 UDP 5060 和 UDP 10000–10019；Docker Desktop、防火墙、设备隔离及 NAT 仍需按实际环境验证。配置了 force_rport、rewrite_contact、rtp_symmetric、外部媒体地址和 direct_media=no，不保证任意网络都可用。

Docker 启动入口仅以 root 读取/复制私有挂载配置并调整所有权，随后 Asterisk 使用 asterisk 用户运行；启动仅保留 CHOWN/DAC_OVERRIDE/SETUID/SETGID 能力。generated 目录由 .dockerignore 排除，随机密码不发送到构建上下文或进入镜像构建层。

## 测试呼叫与查账式恢复

激活已安装后端依赖的虚拟环境，在仓库根目录加载本机实验室环境，再进入 backend：

```bash
. deploy/sip-lab/generated/lab.env
cd backend
python -m app.sip_lab status
python -m app.sip_lab call --request-key test-001 --acknowledged
python -m app.sip_lab inspect --request-key test-001
python -m app.sip_lab hangup --request-key test-001
```

Linphone 接听后播放短提示音并进入回声，通道最多持续 60 秒。也可从 Linphone 主动拨 1000 检查回声。call 的 acknowledged 明确确认只联系自己的测试分机。

工具将发送意图先写入独立 SQLite 日志，再访问 ARI。固定本机地址、固定 endpoint PJSIP/1001、固定 context lab-echo/extension 1000，不能传任意 SIP URI、手机号或目的地。HTTP 不使用环境代理、不跟随重定向、不自动重试，拨号等待 20 秒、HTTP 超时 5 秒。

请求键映射固定 channel ID；同一日志和实例下，同键重复执行只 GET 查询，绝不重发 POST。进程在预留后崩溃、提交超时、409 冲突或未知结果均保留为 reserved/unknown。404 只表示目前没有通道，可能尚未创建，也可能已经结束；不能据此判断未拨号或已完成。未知结果先人工核对 Asterisk/Linphone，不直接更换请求键重拨；请保留日志并勿通过删除日志绕过恢复边界。同实例 UTC 日最多 20 次预留，未知结果也占次数；这是实验室限额，与业务联系次数无关。

hangup 仅允许查询日志中本实例已有请求对应的 channel ID；204 仅表示挂断请求获网关确认，404 仍可能是已结束或尚未建立，不证明成功完成。挂断不释放派发预留，同键后续 call 仍只查询，不重新拨号。

退出码：0 表示当前请求/查询达到其有限条件；2 表示不可用、拒绝或未知；1 表示参数/配置/本地日志错误。任何退出码都不证明音频或 AI 验收。stop：从仓库根目录运行对应 docker compose 命令的 down 子命令。

## 后续接入

下一步接入受控 ARI 事件流及业务任务关联、租户授权/政策复核、真实渠道配额、未知结果对账，再建设 ASR/TTS/媒体桥及结构化对话。SIP 网络验收与 AI 对话验收分开；身份核验、资料真实性、异常保护和到账仍由既有业务流程裁决。

现有 `/channel-providers/*/test` 只有 sandbox 契约测试；live 在开关关闭时 409、打开但缺少适配器时 503。历史 live tested/enabled 摘要显示 blocked，不能继续批准。SIP 回声日志不进入该验收证据。

## 官方依据

- [Linphone 入门与 SIP 账号](https://www.linphone.org/en/getting-started/)
- [Linphone 与 SIP/PBX 兼容性](https://www.linphone.org/en/faq/)
- [Asterisk ARI 通道创建、查询与固定 ID](https://docs.asterisk.org/Latest_API/API_Documentation/Asterisk_REST_Interface/Channels_REST_API/)
- [Asterisk Echo](https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/Dialplan_Applications/Echo/)
- [PJSIP NAT 参数](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/Configuring-res_pjsip-to-work-through-NAT/)
- [Ubuntu 24.04 Asterisk 软件包](https://packages.ubuntu.com/noble/asterisk)

## 软件验证记录

完整后端 333 项：331 通过、2 项 PostgreSQL 专项跳过；其中 SIP 实验室 27 项，渠道 false-positive 修复 2 项，既有电话派发契约 17 项。前端/契约 59 项通过，构建通过；变更文件 Ruff、shell 语法与 Git diff 检查通过。没有 Docker/Asterisk/Linphone，未以模拟 HTTP 结果声称真实媒体或设备联调通过。

## 本轮验证

完整后端 331 项通过、2 项 PostgreSQL 专项跳过；SIP/渠道专项 32 项通过，文档/部署/安全契约 5 项通过。Ruff、diff、shell 语法与 Compose YAML 解析通过。Docker 镜像、SIP 注册、响铃及真实 RTP 未验收。地址生成仅接受 IPv4 回环及 RFC1918 网络；拒绝链路本地、文档保留和公网地址。
