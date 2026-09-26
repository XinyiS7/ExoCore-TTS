# ExoCore TTS — 声音工厂

ExoCore 只认**一个** TTS 端口，这个仓库就是端口的那一侧。本地合成、云端合成、以后换任何引擎，都在**这一侧**解决——ExoCore 本体不受影响，也不需要跟着改。

```text
ExoCore (Django)                        本仓库 = 声音工厂
  │                                      │
  ├── POST /tts {text, voice_key, ...} ─►├── backend: voxcpm2   本地 3060 Ti
  │                                      ├── backend: cloud    （以后加，互不影响）
  ◄── 200 audio/wav（原始字节）───────────┤
```

## 不变量

1. **Django 只给 `voice_key`，永远不给宿主机路径。** 声音长什么样、参考音频放哪、用哪个引擎，都是这一侧的内部事务。换成云端时这条是关键——云端根本没有你本地的文件。
2. **响应保持 `audio/wav` 原始字节**（不是 JSON+URL）。ExoCore 现有适配器已经是按裸 WAV 解析的，契约这样定，接线时只改请求、不动响应解析。
3. **声音资产归本仓所有**：`voices/<key>/reference.wav` + `voice.json`。ExoCore 侧只保存"选了哪个 key"和展示名。
4. **绝不 import Django / 不碰 ExoCore 的数据库。** 两个进程通过 loopback HTTP 说话。
5. **重型依赖不出本仓**：torch / voxcpm 只活在 `voxcpm_runtime` 这个 conda 环境里，永远不进 `ExoCore/requirements.txt`。

## 端口契约（M2 实现，先冻结形状）

**`POST /tts`**

```json
{
  "text": "已经投影好的朗读文本（不含 *动作描述*）",
  "voice_key": "sandro_v1",
  "style": "",                                        // 可选，基底风格描述
  "defaults": {"cfg_value": 2.0, "inference_timesteps": 10, "seed": null},
  "format": "wav"
}
```

- 成功：`200`，正文是裸 `audio/wav` 字节。
- 声音不存在：`404 {"error": "voice_unknown", "voice_key": "..."}`。
- 服务在跑但模型没就绪（冷启动中）：`503 {"error": "model_not_ready", "state": "loading"}`。
- 简单到无聊是故意的：越薄的契约越换得动后端。

**`GET /health`**

```json
{"status": "ok", "engine": "voxcpm2", "model_state": "cold", "voice_count": 1}
```

`model_state` ∈ `cold | loading | ready`。ExoCore 用它区分"服务没开"和"在加载模型"——这两种情况对用户是不同的话（"语音服务暂时不可用" vs "语音服务正在启动"）。

## 声音资产

```text
voices/sandro_v1/
├── reference.wav    冻结下来的参考音频（选角的产物，要保住）
└── voice.json       display_name / engine / baseline_instruction / prompt_text /
                     generation_defaults / source（哪一批的第几号候选、seed、描述）
```

`voice.json` 与 ExoCore 的 `VoiceProfile` 一一对应：`name ← key`、`engine`、`baseline_instruction`、`generation_defaults`。`prompt_text` 是被朗读的那句原文，做 ultimate cloning（参考音频 + 逐字稿）时要用。

参考音频不一定来自本仓的候选池——云端渲染、手工剪的片段都行，用 `register` 收进来（`pick` 只能收本批候选）：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py register \
    --clip path/to/cloud_render.wav --key sandro_v1 --display-name Sandro \
    --transcript-file path/to/transcript.txt --style "风格指令" \
    --origin "gemini-3.8-flash-tts / voice_xxx"
```

`register` 落地前会做体检（可读性 / 时长 ≥ 3s / 非静音），削顶只警告；逐字稿是**强制项**。

## 里程碑

| | 内容 | 状态 |
|---|---|---|
| **M1** | 仓库骨架 + 声音资产库 + 选角台（本文件描述的工具） | ✅ 已落地 |
| **M1.5** | `register` 子命令（收外部参考音频）+ 资产体检 + 首条正式声线 `sandro_v1` | ✅ 已落地 |
| **M2** | `POST /tts` + `GET /health` 守护进程（`backends/fake` 用于契约测试 + `backends/voxcpm2` 真推理；按需加载、空闲卸载） | 待施工 |
| **M3** | ExoCore 适配器改 key-based + `base_url` 配置化 + 区分「冷启动中」与「服务离线」（跨仓计划落 `ExoCore/Plan/`） | 待施工 |
| **M4** | 工具侧 `send_voice_msg`（落库即开始合成）+ 前端独立语音条 | 待施工 |

## 已裁决口径（Alicia，2026-09-26）

1. **模型不常驻。** 守护进程启动时不加载模型；第一条合成请求才加载。使用频率低，显存优先让给其它用途。冷启动由用户承担（她明确接受）。
2. **前端诚实显示「声音加载中」。** 冷启动与合成期间不许伪造进度，也不许把这种情况说成「服务离线」——这两件事对用户是不同的话。意味着 M3 需要一个独立于 `runtime_offline` 的状态（例如 `engine_warming` + `retry_after_ms`）。
3. **触发时机分两种：**
   - 用户点按钮朗读全文 → **惰性**（点击才合成，接受冷启动）；
   - agent 主动 `send_voice_msg` → **落库时即开始合成**（不等用户点击，前端只是如实展示状态）。
4. **`send_voice_msg` 的 UI 形态 = 独立语音条**（消息上第二个播放器，与正文朗读并存）。UI 要重新设计，但底层先做（M4）。
5. **免冻结的声音资产进 git**（`voices/` 不 ignore）；候选池 `candidates/` 不进。

M2 开工前仍需注意：**冷启动耗时必须实测**后回填上面的口径（ExoCore 侧现有 10 秒超时 + 60 秒假死阈值对冷启动 + 长文本不够用，属于 M3 的契约变更），以及**空闲卸载阈值**（建议默认 30 分钟，设 0 表示不卸载）尚待确认。

## 环境

推理栈只装在它自己的环境里：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m pip install -e .
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v
```

（安装会在环境的 `Scripts/` 里生成一个 `exocore-cast`，`conda activate voxcpm_runtime` 后可直接当命令用；本文件统一用 `tools/cast.py` 举例，两者等价。）

- 环境：`voxcpm_runtime`（Python 3.10 + torch 2.5.1+cu121 + voxcpm 2.0.3）
- 权重：`~/.cache/huggingface/hub/models--openbmb--VoxCPM2`（4.7GB，已下载）
- **冷启动实测 ≈ 42 秒**（权重已在本地缓存、含 torch.compile）——这就是前端「声音加载中」要覆盖的时长。
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
- **多音字错读是随机的。** 实测 `还`（hái）被读成 `huán`；随后同句重跑三次全部正确。→ 靠重试规避，不要为此改写文本。
- **别用 librosa 变速当参考。** 相位声码器会让 0.8× 拉伸失真成“旧磁带”；要慢就在云端按风格指令重渲一条慢的。
- **发音抽检只能当报警器。** 把产出回送给 ASR 转写并与原文比对，能抓住真实异常，但会因语调误报（实测一条完全正确的台词被听成缺字）。单次转写不可信，至少要两次一致；且只把"至少一遍完全一致"当通过（见下节 `verify_audio.py`）。
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
