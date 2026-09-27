# 0003 — TTS 守护进程（M2，重写版）

> **状态**：**部分已实现，等待独立验收**。§8 第 1–4 步已落地并提交：CP-A `668b463`（非 GPU 地基：契约层 / 编排 / 单 worker 生命周期 / 分段 / fake 替身 / 启动门禁）与 CP-B `a82f83a`（`voxcpm.py` 共用低层入口 + `backends/voxcpm2.py` 真实引擎 + casting 零回归）。**仍开放**：§8 第 5 步 delivery 人耳探针、第 6 步文档收尾（`voices.py`）、第 7 步真机冒烟剩余项（§9.2 步骤 4–8）。本文件继续作为端口契约的唯一权威；实现事实以两个 checkpoint 验收包为准。
> **范围**：仅 `ExoCore-TTS` 仓库。
> **计划作者**：gpt-5.6-sol / Solaire
> **Pruning review**：deepseek/deepseek-flash / Ecki
> **依据**：已先读 `0003_review_handoff_solaire.md`，再把旧版 `0003_tts_daemon.md` 仅作为事实库；本计划依据当前源码、声音资产与既有测试独立重建。
> **基线**：CP-B 时点非 GPU 测试 **151/151 OK**（65 个 casting/voices/verify 基线 + 86 新增，连跑 3 次稳定，不加载模型、不联网）；三条正式声音资产均为 `voxcpm2`，生成默认值为 cfg 3.5 / timesteps 16。

## 1. 目标与验收边界

把现有声音资产变成一个可长期运行、可替换后端的本地 TTS 服务：

```text
ExoCore -> POST /tts(text, voice_key, delivery?) -> 200 audio/wav
```

M2 完成后必须成立：

1. ExoCore 只需知道 `voice_key`、待朗读文本，以及可选的自然语言表演意图 `delivery`。
2. 引擎、参考音频、逐字稿、基线风格、生成参数、seed、结构校验和 GPU 生命周期全部留在声音工厂内部。
3. 第一个冷请求可以等待模型完成加载并直接返回音频；不制造 `model_not_ready` 重试协议。
4. 模型加载、推理和卸载不得阻塞 HTTP 事件循环；加载期间 `/health` 仍可响应。
5. 单 GPU 推理严格串行；并发首请求只加载一次模型。
6. 模型加载失败可恢复；已经通过校验并进入 runtime admission 的请求，不会被基于旧状态的空闲卸载误伤。
7. 非 GPU 契约测试全部通过；真机冒烟覆盖中、英、德三条已冻结声线。

关键边界经本计划冻结 [gpt-5.6-sol / Solaire]：

| ExoCore 拥有 | ExoCore-TTS 拥有 |
|---|---|
| 选择哪个 `voice_key` | 后端与 provider 选择 |
| 要朗读的最终纯文本 | 参考音频与逐字稿 |
| 可选的本次 `delivery` | 基线声线配方与生成参数 |
| 请求与成品缓存/产品状态 | seed、结构校验、分段与 WAV 拼装 |
| Markdown 到朗读文本的投影 | 模型/GPU 加载、串行、空闲卸载 |

后端替换不得要求修改 ExoCore 生产代码。

---

## 2. 冻结的 HTTP 契约

> 本节是本端口契约的**唯一权威**。`README.md` 只保留同一形状的精简摘要；两处若冲突，以本节为准，并必须立即修正 README——不允许存在第二个版本。

### 2.1 `POST /tts`

唯一允许的请求字段：

```json
{
  "text": "已经由 ExoCore 投影好的朗读文本",
  "voice_key": "sandro_v1",
  "delivery": "optional engine-neutral performance direction"
}
```

规则：

- `text`：必填字符串；去除首尾空白后不得为空；总长度不超过 `EXOCORE_TTS_MAX_TEXT_CHARS`，默认 **600**。这是单卡独占时间的安全上限，不是目标消息长度；按现有实测仍可能需要数分钟。
- `voice_key`：必填字符串；按现有 `voices.validate_key()` 规则校验，再从工厂资产库读取。
- `delivery`：可选字符串；空白值等价于未提供；上限为 **500** 字符。显式 `delivery: null` 属类型错误（`422 invalid_request`）——省略或空串才是“未提供”。
- 未声明字段一律拒绝。旧版的 `style`、`seed`、`defaults`、`verify`、`format` 不属于生产接口。
- 请求内判定顺序固定为：字段类型/范围（422）→ 声音资产解析（目录/manifest 缺失 404；manifest 不可读 503）→ 后端存在与 `check_asset`（503）→ `delivery` 能力（422）。即坏资产优先于能力拒绝；实现与用例都显式固定该顺序。
- 普通“朗读此消息”不传 `delivery`；未来 `send_voice_msg` 可传自然语言 `delivery` [Alicia / product decision]。
- daemon 不解析 Markdown，不猜测哪句话是动作描写，也不删除代码或斜体；它只朗读收到的 `text`。

成功响应：

- HTTP `200`；
- `Content-Type: audio/wav`；
- body 为完整、可读取的 WAV 原始字节；
- 不把引擎名、设备名、路径或推理参数作为稳定响应头暴露。

稳定错误体统一为单字段 JSON：

```json
{"error": "error_code"}
```

| HTTP | `error` | 条件 |
|---|---|---|
| 401 | `unauthorized` | 配置了 token，但 bearer 缺失或不匹配 |
| 404 | `unknown_voice` | key 合法但**没有该声音资产**（资产目录 / manifest 缺失） |
| 422 | `invalid_request` | 空文本、超限、非法 key/字段/类型等请求错误 |
| 422 | `delivery_unsupported` | 目标后端不能安全实现非空 `delivery` |
| 503 | `engine_unavailable` | **资产存在但不可用**（manifest 不可读、reference 缺失/不可读/为空、generation 参数非法）、后端未实现、模型加载失败、依赖/设备不可用 |
| 500 | `synthesis_failed` | 已进入合成但未能产出有效音频 |

错误体在**所有**状态码下都是这一个形状，`401` 也不例外：不得出现 FastAPI 默认的 `detail` 字段或任何附加键。`503 engine_unavailable` 永远不表示“模型正在加载”（warming 的判定见 §2.4）。

响应不得包含原始异常文本。完整异常只记 daemon 日志，并避免泄漏 token、本机路径、缓存位置或 provider 请求内容。

### 2.2 `GET /health`

成功固定返回 HTTP `200`：

```json
{
  "status": "ok",
  "state": "cold"
}
```

- `state` 只允许：`cold | loading | ready`。
- `cold` 表示服务可接单、重型本地模型尚未加载；不是服务离线。
- `cold` 与 `loading` **一律表示服务在线**，只是重型模型尚未就绪；**只有** `/health` 本身不可达（连接被拒 / 超时 / 无响应）才是“服务离线”。这是产品裁决的硬边界：**warming 永远不得被报告为 offline**；反向也成立——不可用不得被说成“正在加载”。
- 加载失败会清理半成品并回到 `cold`；失败请求本身收到 `503 engine_unavailable`，下一请求可重新尝试。
- 推理期间模型仍是 `ready`；排队/渲染状态由 ExoCore 自己的 render job 表达，不由 daemon health 重复建模。
- 健康响应不含 queue depth、backend 名、CUDA 设备、本机路径、预计加载秒数或异常详情。
- 配置 token 时，健康端点使用同一 bearer 校验。

引擎、设备、分段数、加载耗时、推理耗时和 RTF 仅作为结构化诊断日志存在，不成为 ExoCore 业务分支依据。

### 2.3 本次不增加 `GET /voices`

M2 没有运行时消费者需要它：ExoCore 已保存所选 opaque key，本仓也已有 `cast.py list --voices` 运维入口。删除该端点能避免把 `engine` 或声音 manifest 误变成跨仓契约。以后若 UI 确有动态发现需求，再单独定义只暴露稳定展示信息的接口 [gpt-5.6-sol / Solaire]。

### 2.4 调用方义务与 M3 接线门禁

- 现有 ExoCore `VoxCPM2Runtime` 仍发送 `profile_name/engine/version/reference_audio_path/baseline_instruction`，且固定 10 秒 timeout；它与本契约**明确不兼容**。
- M3 完成前不得仅把旧适配器的 `base_url` 指向 `:8769`；严格字段校验会正确返回 422，这不是 daemon 故障。
- ExoCore 必须在发请求前校验 `text` 与 `delivery` 上限：超限时在自己的 job/tool 边界明确失败并给安全提示，禁止静默截断，也不把可预防的 422 留给用户。
- transport timeout 必须覆盖冷加载与该请求最坏渲染时间。**CP-B 真机实测**：冷加载 53.56 s、首个冷请求总耗时 73.88 s（`Plan/0003_checkpoint_B_acceptance.md`），**M3 的 timeout 预算按 ≥ 90 s 设计**。早期选角 bench 记录的 ≈ 42 s 属过时历史数据点，不再作为设计依据。调用方超时不会取消已经 admission/入队的 GPU 工作；超时后不得立即自动重试，否则会重复占用单 GPU 队列。
- 端口字段的唯一名称是 `delivery`；wire 上不出现 `style` / `emotion` / `seed` 等别名。ExoCore 侧产品面可以继续用别的名字（例如 `send_voice_msg` 的 `emotion`），但必须映射到本字段——不要为了对齐而在 ExoCore 侧改工具名，也不要在 wire 上加第二个名字。

冷启动期间调用方可观察到的现象，及其唯一允许的解读（M3 必须按此实现，不得重新引入 `model_not_ready` 轮询协议）：

| 观察到的现象 | 含义 | ExoCore 行为 |
|---|---|---|
| `/health` 可达且 `state ∈ {cold, loading}`，同时 POST 仍在途 | **warming**：重型模型正在加载 | 保持本次 render job 进行中，如实显示「声音正在加载」；不重试、不重复派发 |
| `/health` 可达且 `state=ready`，POST 仍在途 | 正常渲染 / 单卡排队中 | 显示生成中（无百分比、不伪造进度） |
| `/health` 不可达，或 POST 失败于传输层 | **offline / unavailable** | 如实报「TTS 服务暂时不可用」 |
| POST 返回 `503 engine_unavailable` | 后端 / 依赖 / 资产不可用，**不是 warming** | 按不可用处理：安全文案 + 由用户手动重试 |

---

## 3. `delivery` 的确定语义与 VoxCPM2 能力门禁

`delivery` 是本次表演意图，不是资产基线的替代品；例如语气、节奏、强弱、停顿倾向。它不得承载 cfg、timesteps、模型 ID、provider voice ID 或 vendor tag。

当前源码事实：VoxCPM2 克隆路径只有 `text`、参考/提示音频、提示逐字稿、cfg 和 timesteps；既有实测已证明把括号风格前缀塞进克隆文本会被直接念出来。故本计划禁止把 `delivery` 拼入朗读正文。

Vox 后端冻结前必须完成一个小型能力探测：

1. 同一声音、同一文本分别以无 `delivery` 和有自然语言 `delivery` 合成；
2. 确认音色身份保持、目标语气或节奏确有可听差异；
3. 确认指令本身没有被朗读，也没有污染正文；
4. 将可用映射局限在 Vox backend 内，并由 Alicia 人耳确认。

门禁结果是确定的：

- **探测通过**：Vox backend 内部实现该映射；公共请求不变。
- **探测未通过或结论不清**：Vox 对非空 `delivery` 返回 `422 delivery_unsupported`；不得静默忽略、不得假装成功、不得把指令拼进正文。

fake backend 必须接收同一 engine-neutral 字段，以验证从 HTTP 到 backend 的传递边界；它不需要模拟真实情绪效果。

`delivery` 被保留是为了冻结后端无关的长期 wire seam，不代表当前唯一真实后端 VoxCPM2 必然支持它。若门禁不通过，M2 的本地 Vox 对非空 `delivery` 将持续显式拒绝；后续 M4 不得把它当成本地必备能力 [deepseek/deepseek-flash / Ecki review；gpt-5.6-sol / Solaire approved]。

---

## 4. 服务内部设计

### 4.1 分层

```text
server.py                FastAPI app、bearer 校验、请求/响应与错误映射、CLI 启动
service.py               声音查找、backend 分派、分段、结构校验、WAV 拼装
runtime.py               单 worker 队列、模型状态、加载/推理串行、空闲卸载
text.py                  纯文本分段
voxcpm.py                casting 与 daemon 共用的低层 Vox 加载/生成入口
backends/base.py         最小 backend 协议与音频结果类型
backends/fake.py         确定性、无 GPU/无网络的契约替身
backends/voxcpm2.py      声音资产到 Vox 调用的翻译层
```

`server.py` 保持薄层；不能包含声音资产解析、模型调用、分段、重试或音频拼接逻辑。

Backend 输入只包含工厂内部 `VoiceAsset`、一个文本段、可选 `delivery` 和内部生成上下文；输出为采样数据、采样率等内部音频对象。backend 不返回 HTTP 响应。

### 4.2 Vox 共用入口

把 `casting.py` 当前 `_load_model()` 与 `_synthesize()` 中真正的模型加载/采样能力下沉到 `voxcpm.py`：

- torch / voxcpm 继续惰性 import；
- casting 仍负责候选文件、manifest、CLI 输出；
- daemon backend 直接拿内存采样，不经过临时候选文件；
- 低层结果同时返回采样数据与 casting 现有 manifest 所需的计时、RTF、峰值显存、采样率事实；
- 现有 design/clone/register/pick 行为及 manifest 形状不得改变；
- Vox backend 从 `VoiceAsset` 内部读取 reference、prompt transcript、generation defaults；宿主机路径不出服务边界；
- seed 由 backend 内部生成，调用方不可指定。

### 4.3 分段与拼装

两个限制必须分开：

- `EXOCORE_TTS_MAX_TEXT_CHARS=600`：可部署调整的整次请求安全上限，避免单请求无限占用队列；
- `SEGMENT_MAX_CHARS=120`：代码常量，限制单次本地推理段，遵守当前 3060 Ti 显存实测边界。

分段规则：

- 优先在中、英、德常见句末标点及其尾随引号后切分；
- 处理省略号与连续标点时不得生成空段或丢字符；
- 单句仍超限时，优先回退到上限内最近空白；没有空白才硬切；
- 所有非分隔内容和原标点保持原顺序，分段器不改写文字；
- 同一请求的 `delivery` 对每段一致生效；
- 各段使用同一采样率/通道约束，以代码常量 `GAP_MS=250` 的静音连接；
- 任一段最终失败则整次请求失败，不返回半截 WAV。

### 4.4 M2 质量边界

M2 只实现始终启用的客观结构校验：采样非空、数值有限、采样率和通道合法、最终 WAV 可读取。失败即 `synthesis_failed`，不返回半成品。

静段残留、时长合理性和自动换 seed 重试暂不进入 daemon：现有证据不足以为三条声线冻结可靠阈值，默认关闭但仍实现整套配置与测试只会制造未使用的第二控制面。现有 `tools/verify_audio.py` 保留为真机冒烟辅助；未来质量指标经三条声线验证后另立 scope [deepseek/deepseek-flash / Ecki pruning；gpt-5.6-sol / Solaire approved]。

---

## 5. 并发、状态与生命周期不变量

### 5.1 事件循环与单 GPU 队列

- FastAPI endpoint 只做轻量校验和调度。请求侧（事件循环 / 同步 endpoint 线程）只做字段校验、资产解析、`check_asset` 能力门与分段；**模型加载、逐段推理、结果结构校验、WAV 编码，以及空闲卸载时的设备释放，都在唯一的 runtime worker 线程**执行。
- 唯一例外是关闭路径：`service.close()` 在 lifespan 调用线程（uvicorn 关闭流程）执行——先停 idle monitor、`executor.shutdown(wait=True)` 排空 worker，再在**该调用线程**上释放模型（丢引用 + CUDA cache 清理）。这不改变“单 worker 串行”的前提。
- 重型本地 runtime 使用单 worker 执行器，保证模型加载与 GPU 推理串行。
- daemon 只支持单进程/单 Uvicorn worker；启动入口固定按一个 worker 运行，并在 README 明示禁止通过多 worker 复制模型。
- `/health` 只读取短临界区内的状态快照，不等待模型/GPU worker。

### 5.2 首次加载与失败恢复

对外状态主路径：

```text
cold -> loading -> ready
          |          |
          +-> cold   +-> cold  （满足空闲卸载条件）
```

- 并发首请求只能有一个加载所有者；其它请求进入同一队列。
- 只有加载完成才发布模型引用和 `ready`；推理与排队不增加新的 public health state。
- 加载失败必须清除半成品引用并回到 `cold`；当前请求返回 `engine_unavailable`，下一请求可重新加载。
- 状态更新与模型引用发布顺序必须一致，不能出现 `ready` 但引用为空。

### 5.3 空闲卸载

- `EXOCORE_TTS_IDLE_UNLOAD_SECONDS=1800`（**已实现**；`0` 禁用且不启动监控线程）。30 分钟是本计划基于低频使用与显存让渡作出的默认值，不冒充既有 Alicia 裁决；真机 idle 卸载属 §9.2 步骤 6，仍待收口 [gpt-5.6-sol / Solaire]。
- 请求通过 HTTP 校验后，必须先调用 runtime admission，在共享状态锁内原子登记 pending/活动时间，再交给单 worker；完成后再次更新时间。
- 卸载与请求登记共享短状态锁；真正卸载在 runtime worker 中完成。
- 卸载获得所有权后必须重新检查：当前无加载/渲染、无 pending 请求、最新活动时间确已过阈值。任何一项不满足都取消本次卸载。
- 不允许基于定时器唤醒时的旧快照直接丢模型。
- 卸载完成后释放模型引用并调用 CUDA cache 清理；关闭服务时停止 idle monitor，再有序关闭 worker。

---

## 6. 配置与网络安全

在 `config.py` 增加集中、可验证的 daemon 配置：

| 环境变量 | 默认值 | 约束 |
|---|---:|---|
| `EXOCORE_TTS_HOST` | `127.0.0.1` | 只允许明确的 loopback 地址 |
| `EXOCORE_TTS_PORT` | `8769` | 合法 TCP 端口 |
| `EXOCORE_TTS_TOKEN` | 空 | 设置后所有 HTTP endpoint 要求 bearer |
| `EXOCORE_TTS_IDLE_UNLOAD_SECONDS` | `1800` | `0` 禁用；不得为负 |
| `EXOCORE_TTS_MAX_TEXT_CHARS` | `600` | 必须为正；调用方也须预检 |

`SEGMENT_MAX_CHARS=120`、`MAX_DELIVERY_CHARS=500` 与 `GAP_MS=250` 是 M2 代码常量，不扩张为日常环境配置。bearer 比较使用恒定时间比较（如 `secrets.compare_digest`）。

启动硬门禁：**bind host 不是明确 loopback 地址时一律拒绝启动**；token 只是 loopback 上的额外保护，不能用来放宽网络边界。不能用一个可误解析的 host 名称假定安全。

`pyproject.toml`：

- 增加 daemon 入口 `exocore-tts = "exocore_tts.server:main"`；
- 保留 server 依赖在本仓 optional dependencies；
- 为 HTTP 契约测试声明与现有 FastAPI/TestClient 兼容的 `httpx` 开发依赖；
- 不向 ExoCore/Django requirements 写入任何依赖。

现有 sibling `ExoCore/.env` fallback 只保留给当前云端制作/抽检工具；M2 daemon 不从 sibling checkout 获取运行凭据。

---

## 7. 文件范围与当前状态

> 本节的“预计”写法是计划时点的产物；除下面标 ⬜ 的项外，**清单已全部落地**。交付事实以两个 checkpoint 验收包为准（`Plan/0003_checkpoint_A_acceptance.md` / `Plan/0003_checkpoint_B_acceptance.md`），本文件不复制证据。

计划时点的新增清单（现已落地）：

```text
src/exocore_tts/server.py
src/exocore_tts/service.py
src/exocore_tts/runtime.py
src/exocore_tts/text.py
src/exocore_tts/voxcpm.py
src/exocore_tts/backends/__init__.py
src/exocore_tts/backends/base.py
src/exocore_tts/backends/fake.py
src/exocore_tts/backends/voxcpm2.py
tests/test_server.py
tests/test_service.py
tests/test_runtime.py
tests/test_text.py
```

修改清单的当前状态：

```text
src/exocore_tts/casting.py     ✅ 已落地（CP-B a82f83a）：内部实现下沉 `voxcpm`，CLI / manifest 不变
src/exocore_tts/config.py      ✅ 已落地（CP-A 668b463）：daemon 配置 + 字面量 loopback 启动门禁
pyproject.toml                 ✅ 已落地（CP-A）：daemon 入口 + dev extra（httpx）
src/exocore_tts/voices.py      ⬜ 未做：删除“字段镜像到 Django VoiceProfile”的旧权威说明
```

`README.md` / `AGENTS.md` 属文档收尾：本计划冻结的契约摘要、里程碑状态与运行约束已在 Gate-0 文档修复中更新（见 §8 第 6 步）。

计划外新增（已记入验收包偏差表）：`src/exocore_tts/errors.py`（CP-A §5a）、`tests/test_voxcpm_backend.py`（CP-B §6b）。测试文件可按实现后的职责合并，禁止为了匹配文件清单制造空壳模块。不得修改三条已冻结 WAV，或无必要改写其 `voice.json`。

---

## 8. 施工顺序与当前状态

**已完成**（Builder 已停手，等待独立验收）：

1. ✅ **契约地基**（CP-A `668b463`）：配置对象、稳定领域错误、backend 最小协议、fake backend；`/tts`、`/health`、鉴权与错误边界由 fake 固定。
2. ✅ **文本与音频管线**（CP-A）：总量门禁、分段、静音拼接、客观结构校验。
3. ✅ **runtime 生命周期**（CP-A）：单 worker 队列、状态快照、exactly-once 加载、失败恢复、idle monitor 与安全卸载。
4. ✅ **Vox 能力抽取**（CP-B `a82f83a`）：`voxcpm.py` 共用低层入口、`voxcpm2` backend 进生产 registry、casting 65 例零回归；真机基本链路 smoke 通过（cold → loading → ready → WAV，加载期 `/health` 全程可响应；并发“只加载一次 / 推理串行”由非 GPU 用例钉住，真机未单独留档）。

**仍未完成**：

5. ⬜ **delivery 门禁探测**：按第 3 节执行；只有 Alicia 人耳确认通过才启用 Vox 映射，否则固定为显式 `delivery_unsupported`（现状：已固定拒绝）。
6. 🟡 **文档收尾**：Gate-0 文档修复已更新 README 的契约摘要 / 里程碑 / 单 worker 约束与 AGENTS 里程碑；**剩余** `voices.py` 的旧 Django 镜像说明。
7. ⬜ **真机冒烟剩余项**：§9.2 步骤 4–8（en/de 声线、多段长文本、idle 卸载、load 失败恢复）；真机失败先区分代码缺陷与已知 commit/pagefile 环境不足。

每个里程碑只修改本仓拥有的文件；M3/M4 未授权前不碰 ExoCore 或 Desktop。

---

## 9. 验证目标（不冻结测试实现）

> **当前状态**：§9.1 的各条要求已由 CP-A / CP-B 的实现与用例覆盖（非 GPU **151/151**）；决定性断言清单见两个验收包 §4，本文件不复制证据。§9.2 的真机冒烟只完成了步骤 1–2（步骤 3 的并发由非 GPU 用例覆盖），步骤 4–8 仍待收口。

### 9.1 自动化：不加载模型、不联网

必须覆盖：

- `/tts` 只接受冻结的三个字段；旧 knobs 与未知字段得到 `invalid_request`。
- 空白、总长度超限、非法 key、未知声音、未实现 backend 的状态码与错误体精确一致。
- 成功结果是可读取的 `audio/wav`，fake backend 在相同请求 shape 下可替换真实 backend。
- 无 `delivery` 只走声音基线；有 `delivery` 时 engine-neutral 字段抵达 backend；不支持时显式报错而非忽略。
- bearer 开/关行为，以及任何 non-loopback bind 的启动拒绝（无论是否配置 token）。
- 中英德标点、尾随引号、省略号、超长无标点文本的切分；不丢字、不乱序、每段不越界。
- 多段音频按固定 gap 拼装；任一段失败不返回部分音频。
- 非空/有限采样、采样率/通道与最终 WAV 的客观结构校验；调用方无法覆盖 seed。
- 两个并发冷请求只触发一次 load，且推理不并发。
- 慢 fake loader 工作时 `/health` 无需等待重型 worker，并可观察 `loading`。
- load 失败后不永久停在 `loading`，下一请求能再次尝试。
- fresh request 与 idle unload 竞争时，旧定时判断不能卸掉即将使用的模型。
- health 精确只返回 `status/state`，加载失败可恢复且不泄漏 queue/backend/device/异常。
- casting 原有 design/clone/register/pick、manifest 与原子 WAV 写入全部回归。

执行门禁：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v
```

### 9.2 真机冒烟：RTX 3060 Ti

前置：确认系统空闲 commit ≥ 约 8GB；不足时先处理环境，不能把 `OSError 1455`/segfault 误判为 daemon 缺陷。

验收步骤（**已完成：1–2 的真机 cold 链路与 health 时间线；步骤 3 的并发由非 GPU 用例钉住；待完成：4–8**）：

1. 启动服务，`/health` 为 `cold`，无模型显存占用。
2. 用 `sandro_v1` 发短中文冷请求；请求等待加载并最终返回 WAV；加载期间另一连接的 `/health` 可响应且为 `loading`。
3. 并发提交两条冷请求，确认只加载一次、推理串行、两条均得到各自完整 WAV。
4. 提交 300–600 字的中英混合文本（跨多段且不越过请求上限），确认多段拼接、无遗漏、WAV 参数一致。
5. 分别用 `sandro_v1`、`sandro_en_v1`、`sandro_de_v1` 合成代表性短句；检查可读性、语言正确性和声音身份，必要时用现有抽检工具辅助，但最终听判归 Alicia。
6. 把 idle 阈值临时设为 60 秒，空闲后确认状态回 `cold` 且显存释放；在临界点提交请求，确认不会被陈旧卸载决定打断。
7. 人为制造一次 loader 失败，确认状态回到可重试的 `cold`，恢复环境后下一请求能成功加载。
8. 记录冷加载耗时、逐段推理耗时、RTF、段数和释放结果到验收记录；这些数据不进入公共 HTTP 契约。

---

## 10. 明确不做

- ExoCore 侧 Markdown/spoken-text 投影、缓存、异步 render job、按钮状态与 adapter 接线：M3 跨仓计划。
- `send_voice_msg` 工具和独立语音条：M4。
- 云端 production backend、provider secret 管理或 vendor cue 映射。
- `GET /voices`、admin 强制卸载、伪进度、预计完成时间、job API。
- 多进程、多 GPU、优先队列、请求取消或分布式调度。
- 云端 ASR 自动质检。
- 删除 Django 现有 `VoiceProfile` 旧列或做数据迁移；M3 只需停止把它们当权威，破坏性清理另立计划。
- 为旧版未上线的 `/tts` 草案保留兼容字段或错误码。

---

## 11. Adversarial razor / 消融结论

施工前已主动删去以下非必要设计 [gpt-5.6-sol / Solaire]：

- **删 `GET /voices`**：M2 无消费者，CLI 已能列资产；避免泄漏 backend manifest。
- **删调用方参数覆盖**：`style/seed/defaults/verify/format` 会把 ExoCore 重新绑回具体引擎。
- **删冷启动 503 + 轮询**：ExoCore 后续使用异步 render worker，单次请求等待更简单且不会制造双状态机。
- **删诊断响应头和详细 health**：耗时/RTF/device 留日志即可，不能成为应用依赖。
- **删语义猜测与确认弹窗**：Markdown 投影属于 ExoCore；daemon 不猜动作描写。
- **删主观质量策略与配置面**：现有指标只能报警且尚未跨三条声线冻结阈值；M2 只强制客观结构有效。
- **删 Vox delivery 假实现**：当前证据表明文本前缀会被念出；能力不成立时宁可显式拒绝。
- **不提前抽象多 backend 并行调度**：M2 只有一个真实 GPU backend；保留最小 backend 协议即可。

以上消融后，活跃范围只剩端口契约、可靠单机生命周期、文本/音频管线、fake 契约替身和一条真实 Vox 路径，正好覆盖 M2，不向 M3/M4 扩张。
