# ExoCore TTS — 声音工厂

ExoCore 只认**一个** TTS 端口，这个仓库就是端口的那一侧。本地合成、云端合成、以后换任何引擎，都在**这一侧**解决——ExoCore 本体不受影响，也不需要跟着改。

```text
ExoCore (Django)                        本仓库 = 声音工厂
  │                                      │
  ├── POST /tts {text, voice_key, delivery?} ─►├── backend: voxcpm2   本地 3060 Ti
  │                                      ├── backend: cloud    （以后加，互不影响）
  ◄── 200 audio/wav（原始字节）───────────┤
```

## 不变量

1. **Django 只给 `voice_key`，永远不给宿主机路径。** 声音长什么样、参考音频放哪、用哪个引擎，都是这一侧的内部事务。换成云端时这条是关键——云端根本没有你本地的文件。
2. **响应保持 `audio/wav` 原始字节**（不是 JSON+URL）。ExoCore 现有适配器已按裸 WAV + WAV 头解析，所以 M3 只改请求字段与错误映射（`503` 语义、无 `model_not_ready`），不动成功响应的解析。
3. **声音资产归本仓所有**：`voices/<key>/reference.wav` + `voice.json`。ExoCore 侧只保存"选了哪个 key"和展示名。
4. **绝不 import Django / 不碰 ExoCore 的数据库。** 两个进程通过 loopback HTTP 说话。
5. **重型依赖不出本仓**：torch / voxcpm 只活在 `voxcpm_runtime` 这个 conda 环境里，永远不进 `ExoCore/requirements.txt`。

## 端口契约（M2 冻结形状）

> **权威全文是 `Plan/0003_tts_daemon.md` §2**。本节只是同一形状的精简摘要；两处若冲突，以计划为准并立即修正本节——不允许存在第二个版本。

**`POST /tts`** — 只接受以下三个字段，未声明字段一律 `422`：

```json
{
  "text": "已经投影好的朗读文本（不含 *动作描述*）",
  "voice_key": "sandro_v1",
  "delivery": "可选：本次自然语言表演意图；普通朗读省略"
}
```

- `text`：去首尾空白后非空，且不超过 `EXOCORE_TTS_MAX_TEXT_CHARS`（默认 **600**；调用方也要预检，不要静默截断）。
- `delivery`：空白等价于未提供，上限 **500** 字符；它是表演意图，**不是**基底风格覆盖，也不得承载 cfg / timesteps / seed / 引擎或 provider 标识。
- **当前状态：本地 `voxcpm2` 固定拒绝非空 `delivery`**（`422 delivery_unsupported`）：能力探针已执行、Alicia 听判未通过（证据 `Plan/0003_delivery_probe_result.md`，提交 `0a7f19c`）——这是该产品门的完成结果，不是故障。字段保留给未来的后端；M4 的 `send_voice_msg` 不得把本地 delivery 当必备能力。
- 旧草案的 `style` / `defaults` / `seed` / `verify` / `format` **不是端口字段**（选角 CLI 的 `--style` 是资产制作参数，与端口无关）。ExoCore 侧产品面可以另叫（例如 `send_voice_msg` 的 `emotion`），但必须映射到 `delivery`；wire 上只有这一个名字。
- `delivery` 只接受字符串：显式 `null` 是类型错误（`422`）；判定顺序为字段（422）→ 资产存在性（404）→ 资产可用性（503）→ `delivery` 能力（422）。
- 成功：`200`，`Content-Type: audio/wav`，正文是裸 WAV 字节。**不提供** RTF / 设备 / 分段数之类的稳定响应头（它们只进 daemon 日志）；采样率、位深、声道由工厂决定，调用方不得依赖具体取值。
- 错误体恒定单字段 `{"error": "<code>"}`（没有 `detail`）：

| HTTP | `error` | 条件 |
|---|---|---|
| 401 | `unauthorized` | 配置了 token 而 bearer 缺失/不匹配 |
| 404 | `unknown_voice` | key 格式合法但没有**该声音资产**（资产目录 / manifest 缺失） |
| 422 | `invalid_request` | 空文本、超限、未知字段或类型错误 |
| 422 | `delivery_unsupported` | 目标后端无法安全实现非空 `delivery` |
| 503 | `engine_unavailable` | **资产存在但不可用**（manifest 不可读、reference 缺失/坏/空、generation 参数非法）、后端未实现、模型加载失败、依赖/设备不可用 |
| 500 | `synthesis_failed` | 已进入合成但没产出有效音频 |

**`GET /health`** — 成功时固定 `200`，只有两个字段（配置 token 时凭据错误仍是 `401 {"error": "unauthorized"}`）：

```json
{"status": "ok", "state": "cold"}
```

`state` ∈ `cold | loading | ready`。配置 token 时，健康端点用同一 bearer 校验。本里程碑**不提供** `GET /voices`（无运行时消费者；运维用 `cast.py list --voices`）。**进程模型：只支持单进程 / 单 Uvicorn worker**——禁止用多 worker 复制模型（单卡串行与显存前提）。

**冷启动与「在线」的判定（产品裁决：warming ≠ offline）**

- 第一个冷请求**会等待**模型加载完成后直接返回音频；不存在 `503 model_not_ready`，也不需要轮询重试协议（CP-B 真机实测：加载 **53.56 s**、首个请求总耗时 **73.88 s**）。
- ExoCore 区分两种状态只能靠：`/health` 是否可达（`cold` / `loading` = 服务在线、模型尚未就绪 → 如实显示「声音正在加载」）＋ 自己那次 POST 是否仍在途。
- **只有** `/health` 不可达（连接被拒 / 超时 / 无响应）或 POST 失败于传输层，才是「服务离线」（「TTS 服务暂时不可用」）。**把加载中说成离线是契约禁止的**；反过来，`503 engine_unavailable` 表示真的不可用，永远不用来表示「正在加载」。
- 调用方 transport timeout 必须覆盖冷加载 + 该请求最坏渲染时间：**M3 按 ≥ 90 s 设计**（CP-B 实测冷请求 73.88 s）。超时后不得自动重试（GPU 工作仍在排队，重复请求只会占满单卡队列）。

## 声音资产

```text
voices/sandro_v1/
├── reference.wav    冻结下来的参考音频（选角的产物，要保住）
└── voice.json       display_name / engine / baseline_instruction / prompt_text /
                     generation_defaults / source（哪一批的第几号候选、seed、描述）
```

当前已冻结三条（每条都是「配方 + 参考文案 + 参数」一起定的，缺一不可）：

| key | 语言 | 参考 | 参考文案 | 参数 |
|---|---|---|---|---|
| `sandro_v1` | 中文 | 17.48s | Alicia 自己写的阿莱台词（含称呼「西娅」） | cfg 3.5 / ts 16 |
| `sandro_en_v1` | 英语 | 47.48s | 她最喜欢那条 Gemini 英文的原文独白 | cfg 3.5 / ts 16 |
| `sandro_de_v1` | 德语 | 54.96s | 情绪配方生成的德语长段（含引号强调） | cfg 3.5 / ts 16 |

三条都由云端声线 `voice_nnvw5qprqmz7`（display_name `Ale`，prompted 型，2027-09-26 到期）加 Alicia 的原始风格指令渲染，再用本仓的 ultimate cloning（参考 + 逐字稿）复现。英德两条已由 Alicia 听判通过；中文那条的参考文案换成角色真实口吻后才成立（见下方实测经验）。

`voice.json` 是**本仓的资产真相**：`engine` / `baseline_instruction` / `generation_defaults` 全属工厂，端口上不出现。ExoCore 侧只需要 `voice_key`（外加展示名）；它的 `VoiceProfile.engine` 等旧列不再是对齐权威，M3 起停止按它们推导行为（不做破坏性删列）。`prompt_text` 是被朗读的那句原文，做 ultimate cloning（参考音频 + 逐字稿）时要用。

参考音频不一定来自本仓的候选池——云端渲染、手工剪的片段都行，用 `register` 收进来（`pick` 只能收本批候选）：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py register \
    --clip path/to/cloud_render.wav --key sandro_v1 --display-name Sandro \
    --transcript-file path/to/transcript.txt --style "风格指令" \
    --origin "gemini-3.8-flash-tts / voice_xxx"
```

`register` 落地前会做体检（可读性 / 时长 ≥ 3s / 非静音），削顶只警告；逐字稿是**强制项**。

## 云端参考渲染（`tools/render_reference.py` + `tools/create_cloud_voice.py`）

本地引擎克隆的那些参考音频是**云端渲出来的**，所以这条链路必须能重跑：声线资源
（`tools/cloud/voices/ale.json` 记 id，声线本体由 `tools/cloud/prompts/ale.txt` 定义）＋
逐语言配方（`tools/cloud/prompts/recipe_zh.txt` / `recipe_en_de.txt`）＋ 一段**逐字稿已知**的文本。
工具跑在服务环境 `voxcpm_runtime`（那里有带 `SpeechMetadata` 的 google-genai）：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/create_cloud_voice.py              # 复用（或 --force 重建）声线资源
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/render_reference.py     --record tools/cloud/voices/ale.json --text-file <逐字稿文件>     --style-file tools/cloud/prompts/recipe_zh.txt     --out candidates/<batch> --label <名字> --takes 2
```

API key 只在调用时从 `GEMINI_API_KEY` 或 `ExoCore/.env` 现取：**不进本仓、不打印**，报错信息也过
`scrub_secrets`。渲出来的音频用 `tools/cast.py register` 冻结（逐字稿是强制项）。

## 里程碑

| | 内容 | 状态 |
|---|---|---|
| **M1** | 仓库骨架 + 声音资产库 + 选角台（本文件描述的工具） | ✅ 已落地 |
| **M1.5** | `register` 子命令（收外部参考音频）+ 资产体检 + 首条正式声线 `sandro_v1` | ✅ 已落地 |
| **M2** | `POST /tts` + `GET /health` 守护进程（`backends/fake` 用于契约测试 + `backends/voxcpm2` 真推理；按需加载、空闲卸载） | **CP-A / CP-B 实现已提交，等待独立实现验收**：CP-A `668b463` + CP-B `a82f83a`，builder 证据包已就绪，非 GPU 测试 151/151；delivery 产品门已收口（探针未通过 → 本地固定 `422`）；剩余 `voices.py` 文档收尾、真机冒烟 §9.2 步骤 4–8 |
| **M3** | ExoCore 适配器改 key-based + `base_url` 配置化 + 区分「冷启动中」与「服务离线」（跨仓计划落 `ExoCore/Plan/`） | 待施工 |
| **M4** | 工具侧 `send_voice_msg`（落库即开始合成）+ 前端独立语音条 | 待施工 |

## 已裁决口径（Alicia，2026-09-26）

1. **模型不常驻。** 守护进程启动时不加载模型；第一条合成请求才加载。使用频率低，显存优先让给其它用途。冷启动由用户承担（她明确接受）。
2. **前端诚实显示「声音加载中」。** 冷启动与合成期间不许伪造进度，也不许把这种情况说成「服务离线」——这两件事对用户是不同的话。M3 侧需要一个独立于「服务离线」的 warming 状态；本仓给出的判定依据是 `/health.state ∈ {cold, loading}` ＋「自己那次 POST 仍在途」，不需要 `model_not_ready` 重试协议（见端口契约小节）。
3. **触发时机分两种：**
   - 用户点按钮朗读全文 → **惰性**（点击才合成，接受冷启动）；
   - agent 主动 `send_voice_msg` → **落库时即开始合成**（不等用户点击，前端只是如实展示状态）。
4. **`send_voice_msg` 的 UI 形态 = 独立语音条**（消息上第二个播放器，与正文朗读并存）。UI 要重新设计，但底层先做（M4）。
5. **免冻结的声音资产进 git**（`voices/` 不 ignore）；候选池 `candidates/` 不进。

M2 当前状态：**CP-A `668b463` + CP-B `a82f83a` 实现已提交**、builder 证据包已就绪，**仍等待独立实现验收**（Gate-0 的 PASS 只覆盖契约/文档一致性）；**delivery 产品门已收口**（探针 `0a7f19c` 未通过 → 本地固定 `422`）；**冷加载实测 53.56 s、首个冷请求 73.88 s**（见「环境」），M3 timeout 预算 ≥ 90 s；**空闲卸载默认 `EXOCORE_TTS_IDLE_UNLOAD_SECONDS=1800`**（`0` 表示不卸载；该默认值由 M2 计划裁定，最终仍待 Alicia 确认；真机 idle 卸载属待收口的冒烟）。ExoCore 侧现有 10 秒超时 + 60 秒假死阈值对冷启动与长文本都不够用，属于 M3 必须一起改的契约变更。

## 环境

推理栈只装在它自己的环境里：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m pip install -e .
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v
```

（安装会在环境的 `Scripts/` 里生成一个 `exocore-cast`，`conda activate voxcpm_runtime` 后可直接当命令用；本文件统一用 `tools/cast.py` 举例，两者等价。）

- 环境：`voxcpm_runtime`（Python 3.10 + torch 2.5.1+cu121 + voxcpm 2.0.3）
- 权重：`~/.cache/huggingface/hub/models--openbmb--VoxCPM2`（4.7GB，已下载）
- **冷启动实测**：daemon 加载 **53.56 s**、首个冷请求总耗时 **73.88 s**（CP-B 真机，含 HuggingFace 缓存校验与库内 warmup）——这就是前端「声音加载中」要覆盖的时长，M3 timeout 预算 ≥ 90 s。选角 bench 早期记录的 ≈ 42 s 是过时数据点。
- **机器前置条件：空闲提交内存（commit）≥ ~8GB。** 加载会一次性把 4.58GB 权重读进提交内存；本机 C 盘页面文件只有 3GB（`Win32_PageFileUsage`），实测 4 次加载中有 2 次死于提交内存不足（`OSError 1455` 或直接 segfault），而且发生点都在 `model.safetensors` 刚被映射时。**建议把页面文件提到 16GB**，否则 M2 的守护进程每次冷启动都在赌运气。
- 硬件实测（RTX 3060 Ti，可用显存约 6.8GB）：短/中/长三档全部通过，RTF ≈ 2.1，长段落峰值 ~6.4GB；64 条选角候选实测峰值 5458MB。

## 选角（现在就能做的事）

```bash
# 1. 造一批候选：4 个描述 × 8 条台词 × 2 次抽取 = 64 条
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py design \
    --designs tools/sandro_designs.txt \
    --lines   tools/sandro_lines.txt \
    --out     candidates/round1 \
    --repeats 2

# 2. 听（盲目听！文件名只有编号，描述在 manifest.json 里，避免先入为主）
#    列出这批候选的情况：
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py list --from candidates/round1

# 3. 挑中的冻结成正式声线
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py pick \
    --from candidates/round1 --id 7 --key sandro_v1 --display-name Sandro

# 想用现成音频微调对比（克隆模式）
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py clone \
    --reference path/to/take.wav --prompt-text "那句原文" \
    --lines tools/sandro_lines.txt --out candidates/round2
```

为什么这么设计：

- **Voice Design 不稳定**——同一个描述每次结果都不一样，所以一轮要多抽几次，命中一个满意的就**冻结**成参考音频，之后全部走克隆保持音色稳定。
- **盲听**：编号 + manifest 分离，挑的时候用的是耳朵。
- **可复现**：每条候选都记了 cfg / timesteps / seed / 实际送入模型的文本。
- **可续跑**：manifest 每完成一条就落盘，中断后重跑同一条命令会跳过已完成的。
- **省时间**：模型一批只加载一次；`--dry-run` 可以先看计划不花 GPU。

单条台词的时长大致是 `字数 × 0.35 秒`（RTF ≈ 2.1），一次选角建议 8~12 条台词，别一次跑几百条。

## 实测经验（选角阶段，2026-09）

这些是花过 GPU 时间和 API 次数换来的，别重复踩：

- **参考音频同时承载音色、口音和语速。** 同一批台词、同一 seed，只换参考音频，产出时长会贴着参考走（参考慢则产出慢）。所以：**语速靠参考调，不是靠命令行参数**；口音同理。
- **逐字稿是克隆的钥匙。** 同一个参考，只用参考音频 vs 参考 + 逐字稿（ultimate / combined 模式），产出总时长 25.8s vs 37.9s——参考的节奏只在给了逐字稿之后才被真正继承。只给参考是本模型最弱的模式。
- **括号风格前缀只在 Voice Design 模式有效。** 克隆模式下会把指令当正文念出来（`（加重并略微放慢…）` 真被朗读了）。想控制重点/语气，只能靠参考音频或换后端。
- **参考音频的文案就是克隆的语气来源。** 早期拿自编测试句当参考，克隆学到的是「测试句的语气」；换成 Alicia 自己写的阿莱台词后，中文克隆立刻从「一直在自己的声音附近打转」变成可用。**换文案比调参数有效。**
- **克隆产出比参考快 10–17%（中文实测六对）。** 「参考管节奏」这条在中文上打折扣：同句 17.80s 参考 → 14.72s 产出。嫌快时先把参考放慢，不要先动 cfg。
- **谱形指标不能替代耳朵（否证）。** 200-1k / 1k-4k 能量占比曾被用来解释中频「纸筒感」，但 Alicia 认可的英语克隆谱形与被判「不行」的中文批次几乎相同 → 该指标只能当粗筛，不能当判决。
- **「回声感」可测：静段残留电平。** 同一批里 `cand_0002` 的停顿处残留比同批高 11dB（-50 vs -61.5 dBFS），与耳朵听到的「回声」一致；参考音频与英德克隆都在 -60 以下。适合做成自动质检项（喂 M2 的「验证 → 重试」）。
- **给参考做表层 EQ 修不了染色（否证）。** 实测给中文参考提 +5dB 中高频后，克隆产出的中频堆积毫无变化（0.53 → 0.51）——染色是模型从参考的底层性格里重现出来的。**要换参考，不要 EQ 参考。**
- **`lower register / never bright` 这类指令是双刃剑。** 它让中文参考更暗、中频更堆（200-1k 占比 0.33→0.41），中文产出容易变成「贴着耳朵说话的纸筒」；可是去掉它，低音就不够真。最终成立的组合是「与英德同一套配方 + 角色真实口吻的参考文案 + 17.5s 参考」。
- **多音字错读是随机的。** 实测 `还`（hái）被读成 `huán`；随后同句重跑三次全部正确。→ 靠重试规避，不要为此改写文本。
- **别用 librosa 变速当参考。** 相位声码器会让 0.8× 拉伸失真成“旧磁带”；要慢就在云端按风格指令重渲一条慢的。
- **发音抽检只能当报警器。** 把产出回送给 ASR 转写并与原文比对，能抓住真实异常，但会因语调误报（实测一条完全正确的台词被听成缺字）。单次转写不可信，至少要两次一致；且只把"至少一遍完全一致"当通过（见下节 `verify_audio.py`）。
- **工具的盲区：文件开头那一句。** 实测中文参考开头的"把手给我"，在四种不同配方、以及一条已被 Alicia 认可的旧参考上，**全部**被转写成"巴碩給我 / 把它給我 / 巴秀给沃"之类的乱码。开头的低起音让转写器失效——所以"开头糊"是工具的常态，不是配方的错。判开头只能靠耳朵，别据此改配方。
- **本地引擎英语/德语可用**：逐字正确（含德语变音符号），RTF ≈ 2.0–2.8（比中文便宜）。但参考决定语言与口音，所以**每种语言需要各自的参考音频**（多参考资产尚未实现）。

## 发音/文字抽检（`tools/verify_audio.py`）

"引擎到底有没有把台词念对"靠耳朵抽检太慢，所以有一个自动报警器：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/verify_audio.py --batch candidates/final_test --language zh
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/verify_audio.py --clip foo.wav --text "那句原文" --language en
```

把产出回送给云端模型转写，再与 manifest 里的原文比对（语言支持 `zh` / `en` / `de`，默认两遍转写）。**它是报警器，不是判决书**：转写器会把语调听成内容，实测一条完全正确的台词被听成缺字。判定只有三级：

| 判定 | 含义 |
|---|---|
| `ok` | 至少一遍转写与原文完全一致——内容在，不需人工 |
| `listen` | 没听全但内容大体在，大概率是语调所致，值得人耳一听 |
| `suspect` | 内容本身没出来（丢词 / 串音 / 元音发错） |

退出码：出现 `suspect` 为 1（便于 M2 接进验证环节）。需要云端 key，默认读隔壁 `ExoCore/.env`，可用 `EXOCORE_TTS_DOTENV` 覆盖。

## 与其它仓库的关系

- 本仓是独立仓库（`git@github.com:XinyiS7/ExoCore-TTS.git`），本地挂在 `ExoCore_Project/ExoCore-TTS/`。
- 与 `ExoCore-Runtime` 同属"主仓旁边的服务仓"这一类：各自独立启停、独立环境、loopback HTTP 契约。
- 跨仓改动（本仓 + Django）的计划统一落 `ExoCore/Plan/`，不在这里另起一份。
