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

## API / Worker 内部回声关联

主机 CLI 继续独立运行；新增业务队列关联路径见 [MVP](../standard-loan-machine-collection-mvp.md)。显式设置 SIP_LAB_TENANT_ID 后，管理员可在沙箱会话关联固定测试分机；该关联不证明合成案件或来源真实性，也不发送任何案件/债务字段。API 不解析客户 contact_reference，更不能调用普通手机号。

迁移 037 新建独立持久派发记录。测试配额按北京时间统计，独立于主机 CLI 的 UTC 日限制。两条测试路径不要混为一个配额或验收报告；生产跨渠道配额仍待实现。

### 停止请求与派发并发

停止 API 及政策、资料、会话变化触发的停止任务，会在同一事务立即持久化 `stop_requested`（未发起的 `prepared` 仍变为 `blocked`）。Worker 在派发意图提交后、重新取得租户与案件锁时刷新派发记录；若状态已变化，禁止原始 POST。已经开始的请求按锁顺序完成，再由停止任务 DELETE 原固定通道。查询保留停止回执，不恢复派发资格，也不将 404 当成音频验收证据。

### Asterisk 启动权限修复（2026-10-08）

设备联调发现 `install: cannot change permissions ... Operation not permitted`：`install -o/-g -m` 在交接所有权后修改模式，受容器最小能力限制。启动脚本改为 root 创建并设置 0600，然后单独 chown 给 asterisk；保留现有 Compose 能力和非 root 服务运行。拉取修复后须 `up --build -d --force-recreate`，无需重新生成配置或修改凭据。实验室回归 27 项通过；新增能力回归因本环境仅映射 UID/GID 0 跳过，Docker 启动仍需设备验证。

### Docker Desktop LAN 媒体地址修复（2026-10-08）

设备 SDP 回执发现 200 OK 宣告容器私有地址而非主机 LAN 地址，手机无法按该地址回传 RTP。生成器在固定 1001 endpoint 显式设置 media_address 为已验证主机地址，并保持 bind_rtp_to_media_address=no（主机地址不属于容器网卡）、media_encryption=no。现有 generated 不自动覆盖，需只更新 endpoint 媒体设置、保留密码，并重建容器加载配置。加密呼叫 RTP/SAVP 被拒绝时先关闭 Linphone 媒体加密；明文成功的 RTP/AVP SDP 应使用电脑 LAN 地址。采集 SIP 日志不要共享 a=crypto 行或认证头；只取 c=、m=、rtpmap、状态码与 RTP 收发。实验室回归 29 通过、1 个能力测试因 UID 映射跳过；真实回声待设备复测。

## 内部音频阶段与诊断

操作者报告主动拨打回声分机和系统呼入固定分机均可听到回声。这是人工观察，不修改 CLI/API 的 audio_verified=false，不是完整 AI 或生产验收。

从 backend 已加载实验室环境执行 `python -m app.sip_lab doctor`，仅查询固定分机并读取本地配置。输出布尔检查，不输出地址或凭据；本地配置通过不证明运行配置已加载。status/doctor 不创建呼叫日志。

`python -m app.sip_lab_diagnostics` 从标准输入提取 RTP 收发、音频协商及 SIP 状态计数，不输出认证信息、媒体密钥、地址或通道标识。超过 1 MiB 拒绝处理，多呼叫不做单呼叫关联，计数不证明音频验收。专项 60 项通过，1 个能力测试跳过，Ruff 通过。

## RTP 接收解码基础（内存组件）

新增 `app.sip_lab_media`：按 RFC 3550 验证 RTP v2 头、CSRC、扩展和 padding；仅接受已明确绑定的 SSRC 和静态 PT0/PCMU、PT8/PCMA。输出 8 kHz 单声道 PCM S16LE，单包音频最多 960 个样本、报文最多 4096 字节。丢弃重复与乱序、时间戳重叠/逆行及超过一秒的跳变，支持序号与时间戳回绕；拒绝包不推进流状态。接收预算最多 60 秒样本，统计缺包但不伪造丢失音频。帧 repr 不含音频，摘要不含 SSRC 或原始 payload，不保存或记录音频。

该解码组件本身不监听 UDP/ARI，不识别任意加密 payload，调用者必须先验证协商为明文且绑定合法来源。没有 jitter buffer、丢包恢复、RTCP、ASR/TTS 或模型调用。当前 CLI/API 的音频/AI 验收状态保持原边界。下节新增独立固定分机网络适配器；业务任务媒体接入仍需停止与租约检查。

依据：[RFC 3550](https://www.rfc-editor.org/rfc/rfc3550.html)、[RFC 3551](https://www.rfc-editor.org/rfc/rfc3551.html)。合成 G.711 全部码字与独立参考比较，覆盖已知采样向量、异常包、跨流输入、回绕、乱序、重叠时间戳和预算；不使用真实通话音频。

诊断、媒体与派发专项合计 86 项通过，1 个容器能力测试因环境限制跳过，Ruff 通过。未重跑全库或真实 ASR/TTS 联调。

## 快速验证一：程序网络媒体桥（1002）

2026-10-08 新增可运行的 `app.sip_lab_bridge`，保留原 1000 的 Asterisk 回声。手机仍用已注册的 1001 账号、UDP 和关闭媒体加密，改拨 1002。本步骤不调用云服务。

在仓库根目录更新并启动独立媒体容器，重建实验室使 1002 拨号计划加载：

```bash
git pull --ff-only
SIP_LAB_MEDIA_ACKNOWLEDGED=true docker compose \
  --env-file deploy/sip-lab/generated/compose.env \
  -f deploy/sip-lab/compose.yml --profile media up --build -d --force-recreate
docker compose --env-file deploy/sip-lab/generated/compose.env \
  -f deploy/sip-lab/compose.yml --profile media logs --tail=40 media
```

等待 `awaiting_linphone_1002` 后拨 1002。先听两段不同频率的提示音，再说固定测试短句，应该听到音量降低的回声；每次最多 60 秒，仅一个通话。主动挂断，再执行上述 logs 命令。成功路径依次出现 `program_media_connected`、`media_session_summary`，其 `received_packets`、`decoded_samples`、`sent_packets` 应大于 0；结合人工听到双提示音与程序回声才能记录这一层通过。只看到发送计数不能证明回传，计数也不能证明 ASR/AI 成功。

真实路径为手机 SIP/RTP ↔ Asterisk 固定分机 ↔ ARI ExternalMedia ↔ media 容器 UDP/PCMU ↔ PCM 解码、减半、重新编码。媒体容器不发布额外主机端口；只接受 ARI 返回且匹配 Asterisk 容器地址、10000–10019 端口的来源，并固定首个合法 SSRC。队列最多 10 帧，20 ms 发送一帧，调度迟滞不补发洪水；不录音、不输出地址或原始包。

媒体资源创建前保存独立私有 journal，UTC 日最多 20 次预留。重启仅清理 journal 中原有通道和桥，不重建未知请求；未确认清理则停止，不自动重启。journal 是独立 Docker 命名卷，不是业务数据库或原呼出日志。容器启动时读私有配置后降权运行。构建上下文只包含固定应用模块，不包含 generated 或本地模型权重。实验室无业务租户、Worker 授权或客户目标解析，因此不能用于业务会话。

异常时只共享媒体容器输出的上述摘要事件。`media_setup_failed` 表示 ARI 设置失败并已清理；`media_lab_unavailable`/`cleanup_unknown` 需要先核对网关与现有资源，不通过删除卷规避恢复。如果 1002 立即挂断，先确认媒体容器存活、等待事件已出现以及新拨号计划已加载。

停止媒体容器：

```bash
docker compose --env-file deploy/sip-lab/generated/compose.env \
  -f deploy/sip-lab/compose.yml --profile media stop media
```

保留 journal 卷；不要使用 `down -v` 重置实验室配额。依据：[Asterisk ExternalMedia 官方说明](https://docs.asterisk.org/Development/Reference-Information/Asterisk-Framework-and-API-Examples/External-Media-and-ARI/)。本轮仅运行本机 UDP socket 与模拟 ARI 验证，Docker 构建及手机上的 1002 听音仍需操作者验证。

## 快速验证二：百炼合成 ASR→LLM→TTS

`app.sip_lab_voice_probe` 是独立的收费云连通探针，不连接手机、案件或账本。固定测试源“今天是语音链路测试”：TTS 生成内存 PCM → ASR 识别 → 精确匹配去标点后的短句 → LLM 返回受限 JSON → TTS 合成回复。每次最多四次服务调用，无自动重试；每次 TTS 输出最多 10 秒、回复最多 60 字、LLM 最多 128 输出 token、读取响应最多 64 KiB，阶段间及接收检查 60 秒总预算。网络连接、关闭和系统调度可能增加退出耗时。

在已激活的后端 Python 3.12 虚拟环境中，从仓库根目录执行：

```bash
. deploy/sip-lab/generated/lab.env
cd backend
python -m pip install -r requirements.txt
ENABLE_SIP_LAB_VOICE_TEST=true python -m app.sip_lab_voice_probe --acknowledged
```

在本机不回显提示中输入有相应模型权限的**百炼北京地域** API Key，勿粘贴到聊天或写进 generated 配置。若已通过秘密注入设置 `DASHSCOPE_API_KEY`，探针使用该变量。WS/HTTPS 仅访问固定北京端点，不接受 URL 覆盖，不使用环境代理或重定向。合成音频只留内存，输出仅模型名、阶段耗时、样本数及回复摘要，不保存录音、原文、API Key 或服务端错误正文。

退出 0 且 `service_chain_completed=true`、`asr_phrase_matched=true` 表示本次云服务短句链路完成。`phone_audio_verified=false`、`business_ready=false` 始终保留。失败输出 `failed_stage` 和固定 `error`（例如短句不匹配、响应超限或网络不可用）；`completed_stages` 指此前已经完成的阶段。

| 环节 | 首轮固定模型与参数 | 参考与后续对比 |
|---|---|---|
| ASR | fun-asr-flash-8k-realtime-2026-01-28，PCM S16LE/8 kHz | 适配中文电话低采样率；后续用同批合成样本对比 Qwen-ASR，不先假定更优 |
| LLM | qwen-plus-2025-12-01，enable_thinking=false，JSON | 先验证结构与指令遵循；后续对比 qwen-flash-2025-07-28 的实测耗时、正确率和费用 |
| TTS | cosyvoice-v3-flash，longanyang，PCM/8 kHz | 先免重采样验证；后续对比其他有权限的音色与 Plus 版本的电话可懂度 |

官方依据（查询日期 2026-10-08）：[Fun-ASR 8K](https://help.aliyun.com/en/model-studio/fun-asr-flash-8k-realtime)、[ASR 客户端事件](https://help.aliyun.com/zh/model-studio/fun-asr-client-events)、[ASR 服务端事件](https://help.aliyun.com/zh/model-studio/fun-asr-server-events)、[Qwen Plus](https://help.aliyun.com/zh/model-studio/qwen-plus)、[Qwen Flash](https://help.aliyun.com/zh/model-studio/qwen-flash)、[CosyVoice 客户端事件](https://help.aliyun.com/zh/model-studio/cosyvoice-client-events)、[系统音色](https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list)。TTS 使用平台版本名，尚无此探针固定的日期快照；须在验收时记录模型名、音色、地域和时间。

建议正式链路为电话 RTP → PCM → 流式 ASR → 句末/VAD → 确定性对话状态机 → LLM 结构化候选 → 当前政策检查 → 分句 TTS → PCMU/RTP 回传。拒绝联系、争议与身份未核验由状态机裁决；模型输出不得直接修改账务、承诺或拨号资格。还要补独立的打断控制（取消旧 TTS 并清空未播队列）、多轮上下文和业务 Worker 停止联动。

当前探针按阶段完成后串行调用 LLM，阶段耗时包含连接与完整输出，不是首 token 延迟或手机端到端延迟。两项快速验证通过后仍需把电话媒体和云服务适配器连接起来。后续用至少 30 轮固定合成对话记录“用户停说到首段回复”的 P50/P95、关键数字/日期/否定句、打断和停止效果；建议 P95≤2 秒作为初始调优目标，尚非实测结果或 SLA。

本轮后端完整回归 435 项通过、3 项跳过（2 项 PostgreSQL、1 项容器 UID/GID 能力）；新增媒体桥与云探针 37 项通过，Ruff、Compose YAML 与构建路径检查通过。实际本机 UDP socket 音频往返已验证，ARI 与云模型服务用模拟协议响应验证；无 Docker 运行或真实云 Key，未声称设备桥接、收费模型或完整电话 AI 通过。

## Mac Studio 本地模型与电话对话（1003）

针对操作者 M3 Ultra / 512 GB 配置新增 Mac 原生 `app.sip_lab_local_voice` 与独立 1003 入站适配。此路径仅联系自己的 Linphone 1001，用于语音测试；没有客户案件、账务、真实渠道启用或业务 Worker 关联。上节百炼云探针继续作为独立对照，不是本地服务的依赖。

| 环节 | 固定本地模型 | 处理 |
|---|---|---|
| ASR | mlx-community/Qwen3-ASR-1.7B-8bit | 电话 PCM 8 kHz 抗镜像上采样到 16 kHz；句末后转写，最多 128 token |
| LLM | mlx-community/Qwen3-30B-A3B-Instruct-2507-8bit | 非思考、固定测试提示、最多 128 token；reply/end JSON 校验，回复最多 60 字 |
| TTS | mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit | 固定 Vivian 中文预置音色；分块生成 24 kHz，再抗混叠转换为电话 8 kHz |

本地模型服务原生运行于 macOS，不放入 Linux 容器。SIP 与 RTP 继续由 Asterisk/media 容器处理。首轮优先低延迟测试，512 GB 容量不代表已测并发能力。代码通过可选依赖延迟导入 MLX，不改变生产后端镜像或云方案。

### 首次准备（Mac 终端 A）

确保安装原生 arm64 Python 3.12，已有 SIP generated 配置保持原样。在仓库根目录：

```bash
git pull --ff-only
python3 scripts/sip-lab-local-voice-config.py
python3.12 -m venv backend/.venv-local-voice
. backend/.venv-local-voice/bin/activate
python -m pip install -r backend/requirements-local-voice.txt
. deploy/sip-lab/generated/lab.env
. deploy/sip-lab/generated/local-voice.env
cd backend
python -m app.sip_lab_local_voice prepare --acknowledged
python -m app.sip_lab_local_voice serve --acknowledged
```

凭据生成只新增 0600 的 local-voice.env，已有文件拒绝覆盖，不改 SIP 密码与原日志。只有首次需要执行生成命令。模型准备会下载较大权重，需要本机网络及磁盘空间；会获取三个固定模型的具体 Git revision，下载到默认 ~/.cache/repayguard-voice/{asr,llm,tts}/{revision}，全部完成后原子写 manifest.json。可通过本机 SIP_LAB_MODELS_DIRECTORY 改缓存根目录，不接受模型或服务地址覆盖。模型权重、缓存与真实凭据不提交仓库。

依赖固定到本轮核对的 mlx-audio/mlx-lm 源码提交，使用专用虚拟环境，勿混入业务后端环境。serve 只加载 manifest 中固定模型及 revision 目录，强制 HF 离线模式，不在通话中下载权重；拒绝自定义 auto_map 及 Python 模型代码。启动预热三项模型后输出 local_voice_ready，表示加载及预热完成，不等于电话/业务验收。local_voice_unavailable 附带固定 stage/error，第三方异常原文不输出。

服务仅监听 127.0.0.1:8090/lab/voice，要求独立 Bearer token，禁止浏览器 Origin，不接受跨域网页或任意 HTTP 模型请求。Docker Desktop 使用 host.docker.internal 访问主机服务；该路由仍需在操作者 Mac 验证，不应改为公网或无认证服务来绕过连接失败。模型服务保持终端 A 前台运行。

### 合成自测与手机对话（Mac 终端 B）

新终端回到仓库根目录，加载同一套环境：

```bash
. backend/.venv-local-voice/bin/activate
. deploy/sip-lab/generated/lab.env
. deploy/sip-lab/generated/local-voice.env
(cd backend && python -m app.sip_lab_local_voice probe --acknowledged)
SIP_LAB_MEDIA_ACKNOWLEDGED=true SIP_LAB_MEDIA_MODE=voice \
SIP_LAB_LOCAL_VOICE_ACKNOWLEDGED=true docker compose \
  --env-file deploy/sip-lab/generated/compose.env \
  -f deploy/sip-lab/compose.yml --profile media up --build -d --force-recreate
docker compose --env-file deploy/sip-lab/generated/compose.env \
  -f deploy/sip-lab/compose.yml --profile media logs -f media
```

probe 的固定 TTS 短句→ASR 精确匹配→LLM→TTS 完成后，返回 service_chain_completed=true、asr_phrase_matched=true；音频仅在内存消费，无云调用。该结果仍带 phone_audio_verified=false、business_ready=false。

等 awaiting_linphone_1003 后，用原 1001 账号拨 1003。应听到本地生成的测试问候；依次说“今天是语音链路测试”“请复述数字一二三四”“结束测试”。最后一句由确定性规则结束测试，结束语播完再挂断。再呼入一通，回复播放中插话说“换一句测试短句”，检查旧声音停止及新回复。挂断后核对 media_session_summary 中 local_turns_completed>0、local_voice_failed=false 和 interruptions；各项仍须人工听音，不能只看计数。1002 在 voice 模式仍可作程序回声对照；同一 media 容器仅一个通话，1002/1003 不并行。

### 当前控制与验收范围

- 一个模型 WS 会话独占原生服务；每通最长 60 秒，包含问候最多 8 轮，句子最多 6 秒。模型服务连接预算 65 秒、每轮在推理边界检查 20 秒预算；不承诺中途硬抢占 MLX 内核。未知派发仍沿用独立媒体 journal，只清理原资源，不重建呼叫。
- 能量 VAD 连续 60 ms 达阈值开始说话，至少 200 ms 有声才转写，600 ms 静音结束；保留最多 100 ms 预录。初版是句末 ASR，尚非连续实时 partial ASR，阈值与端点需按手机实测调优。
- 插话立即清除播放队列、发送取消并使旧轮次失效；等待原生推理退出后才能开始下一轮。生成过程中按 token/chunk 检查取消，不能保证即时中断正在运行的 ASR/GPU 内核。生成尚未完成的取消轮次不加入后续模型上下文；已生成完成但播放被打断的回复仍保留在上下文中。
- 内存上下文最多四组问答；原生输出队列最多八块、客户端播放队列最多 500 帧（10 秒），超限失败而非无限积压。模型/网络失败使本通结束，不自动转云、不自动重拨。
- 这是内部语音助手，不提供债务说明、本人核验、承诺登记或账务动作；结束与金融话题阻断先于 LLM，结构校验再决定播报。没有客户政策与业务工具调用。
- 初版没有声学回声消除（AEC）或 jitter buffer。先使用耳机验证打断；手机免提的扬声器回声可能触发 VAD，不能视为真实用户插话已正确识别。分块重采样的接缝、自然度与数字日期仍需真机录入固定测试短句后听音核验。

停止顺序：先使用前文 stop media 结束通话并保留 journal，模型服务再 Ctrl+C。服务重启清空内存上下文；已有不确定媒体资源不重新创建。原生内核尚未退出时继续占用模型执行门禁，避免新会话与旧推理并行。

本轮软件证据包括真实本机 TCP/WebSocket→程序→UDP/PCMU 回传，模型使用可控测试替身；另验证实际 SciPy 抗镜像/抗混叠转换。不能据此声称 MLX 权重加载、Mac GPU、Docker Desktop 路由、手机多轮、打断或性能目标已通过。各阶段耗时从服务开始推理计到首块/完成，不包含手机端点等待和全部播放，不是完整端到端 P95。

软件验证：完整后端回归 460 项通过、3 项环境依赖跳过；随后补充断连后不可抢占推理仍持有门禁的用例，最终桥/云探针/本地语音专项 63 项通过。独立原生 DSP 两项、文档/部署/依赖清单五项通过，Ruff 与 Compose YAML 检查通过。DSP 专项在安装可选原生依赖后可从 backend 执行 `python -m unittest discover -s tests_local_voice -v`，不要求加载模型。

官方/维护者依据：[MLX LM](https://github.com/ml-explore/mlx-lm)、[MLX Audio](https://github.com/Blaizzy/mlx-audio)、[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[非思考 LLM 模型](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507)、[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)。量化模型来源固定为表中的 mlx-community 模型，具体 revision 在本机准备时记录；本轮没有下载或发布真实模型权重。
