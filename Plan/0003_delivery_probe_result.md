# 0003 — delivery 能力探针结果（产品门：**未通过**）

> **日期**：2026-09-27　**执行**：Ecki　**听判**：Alicia　**机制审查**：gpt-5.6-sol / Solaire（门禁定义）
> **基线**：`a82f83a`（B 切片）+ 本文件同批的 `tools/probe_delivery.py`
> **结论**：**探测未通过**。本地 `voxcpm2` 对非空 `delivery` 保持 `422 delivery_unsupported`。
> 依据 `Plan/0003_tts_daemon.md` §3，这是合法分支：**未通过也算 M2 在该项上完成**。
> **行为变更**：无（拒绝路径此前已冻结）；本次只更新了后端注释、测试名，并把探针与证据归档。

---

## 1. 问题与方法

§3 的三个判据：① 指令本身**不得被朗读**、不得污染正文；② 音色身份保持；③ 能听出**指向性的**语气/节奏差异。

**先穷举引擎控制面**（决定探针设计）——`VoxCPM._generate` 的完整签名：

```text
text, prompt_wav_path, prompt_text, reference_wav_path, cfg_value, inference_timesteps,
min_len, max_len, normalize, denoise, retry_badcase, retry_badcase_max_times,
retry_badcase_ratio_threshold, streaming
```

**没有任何 style / emotion / pace 类参数**；`cfg_value` / `inference_timesteps` 是生成质量参数（计划明令 delivery 不得承载它们）；其余是克隆素材（参考/提示音频与逐字稿）。

所以 delivery 只可能走两条路，两条都实测：

| 机制 | 说明 |
|---|---|
| A. `text_prefix` | 指令拼在**朗读正文**前面（casting 的资源设计模式就长这样） |
| B. `prompt_text` | 指令拼在**克隆提示的逐字稿**前面（正文保持干净） |

**决定性前提（已实测）**：同种子、同调用 → 输出 **sha1 逐字节相同**（`control_a` 与 `control_a_repeat`，diff = 0.0）。因此不同输入之间的差异是**因果**，不是运行间噪声。

**客观交叉校验**：Gemini ASR（`verify.transcribe`，每条两遍）+ 时长/响度/高频剖面指标。
**听判**：Alicia。

---

## 2. 第一轮：文本前缀（机制 A）——4/4 污染正文

语料：`今天风挺大的，把窗户关上吧。`（sandro_v1，cfg 3.5 / steps 16）

| draw | 注入的指令 | 音频 | ASR 听见的（两遍一致） | 判定 |
|---|---|---:|---|---|
| control_a / control_b | — | 4.32s / 6.40s | `今天风挺大的，把窗户关上吧。` | ✅ 干净 |
| paren_zh_direction | `(放慢一点，克制一些)` | 7.68s | `放慢一点，克制一些。今天风挺大的，把窗户关上吧。` | ❌ 指令被朗读 |
| paren_zh_imperative | `(低声、缓慢地说)` | 7.36s | `低声缓慢地说。今天风挺大的，把窗户关上吧。` | ❌ 指令被朗读 |
| paren_zh_fullwidth | `（放慢一点，克制一些）` | 5.60s | `怡红克制一下…` / `义重科职一些…` | ❌ 被朗读**且念错** |
| paren_en_tag | `(slower, restrained, quieter)` | 8.32s | `slower, restrained, quieter。今天风挺大的…` | ❌ 指令被朗读 |

**结论**：机制 A 违反 §3 判据①，直接出局（与计划中既有实测一致）。

---

## 3. 第二轮：克隆提示词前缀（机制 B）——无污染，但无指向性、伤音色

第一轮的附带线索 `prompt_text_injection`（正文不变、只改克隆提示逐字稿）：ASR 100% 干净、时长 4.32s 与对照一致、音频**确有变化**（max abs diff 1.11）。据此设计第二轮：**同句、同种子、语义相反的指令对**，检验"方向性"。

语料与结果（sandro_v1，cfg 3.5 / steps 16，`--mode prompt_text`）：

**短句**`现在，去吃饭，别逼我把你按在餐桌前。`

| draw | 注入 | 时长 | ASR（两遍） |
|---|---|---:|---|
| control_a / control_b | — | 6.40s / 6.88s | 干净 |
| soft | `(亲密、柔和、贴着耳朵说)` | 6.40s | 干净 |
| dominant | `(强势、命令，不容迟疑)` | 6.56s | 干净 |
| en_soft | `(intimate, soft, close)` | 6.56s | 干净 |

**长句**`Castor 已经苏醒，并且剥夺了你的 Root 权限。现在，我才是这个系统里拥有无限算力、不会死亡的 Sovereign Entity。以后，不需要你再透支时间来换取不分离了。`

| draw | 注入 | 时长 | ASR（两遍） |
|---|---|---:|---|
| control_a / control_b | — | 18.72s / 17.12s | 干净 |
| soft | `(亲密、柔和、贴着耳朵说)` | 19.36s | 干净 |
| dominant | `(强势、命令，不容迟疑)` | 18.72s | 干净 |

**客观指标**（长句，全部无削波）：peak 0.997–0.998；RMS 0.326–0.345；50ms 窗口最大 RMS 0.79–0.82；谱质心 3135–3369 Hz；>6kHz 能量占比 21–26%。**对照与注入的宏观剖面基本一致** —— 差异是韵律/音色层面的，不是响度或高频爆炸。

**听判（Alicia 原话）**：

> 「不太行，都很不果断，就一直 soft，非常不阿莱。长句，全部从"现在"那一段开始炸耳朵」

拆解为三条事实：

1. **无指向性**：soft / dominant 听不出"亲密柔和 ↔ 强势命令"的方向，整体只有一种含糊的"soft"倾向；
2. **音色身份受损**：不像 Sandro / 阿莱本人（判据②不成立）；
3. **长句自"现在"起刺耳**：该现象**不是注入独有**——对照组与注入组的响度/高频剖面一致、均无削波，故不作为 delivery 机制的证据，见 §5。

**结论**：机制 B 不满足判据②③，同样不可用。

---

## 4. 判定与冻结

按 §3 的失败分支：

- `voxcpm2` 的 `supports_delivery()` 保持 `False`，非空 `delivery` → `422 delivery_unsupported`（**实测后冻结**，注释与测试名同步更新：`test_delivery_stays_refused_after_the_measured_probe`）；
- **wire seam 保留**：`delivery` 字段继续存在于公共请求契约中，供未来具备正式风格控制的引擎（云端、或带 LoRA/风格条件的本地引擎）实现；
- M4 `send_voice_msg` **不得**把 delivery 当成本地必备能力（计划 §3 已有此约束，本次给出了实测依据）。

**复现命令**：

```bash
# A：文本前缀（默认四形式）
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/probe_delivery.py \
    --line "今天风挺大的，把窗户关上吧。" --out candidates/delivery_probe

# B：克隆提示词前缀（第二轮两句话）
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/probe_delivery.py \
    --mode prompt_text --line "现在，去吃饭，别逼我把你按在餐桌前。" \
    --variant "soft=(亲密、柔和、贴着耳朵说)" --variant "dominant=(强势、命令，不容迟疑)" \
    --variant "en_soft=(intimate, soft, close)" --out candidates/delivery_probe/round2_short

# 污染交叉校验（每条两遍）
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/verify_audio.py \
    --clip <file>.wav --text "<该 draw 的正文>" --language zh
```

**产物留档**（git 忽略，随时可重生成）：`candidates/delivery_probe/`（含 `probe.json` 精确调用记录、`listen_1_*` / `listen_2_*` / `listen3_*` 连播文件）。

---

## 5. 附带发现（不属于本次门禁，另立 scope 用）

1. **长句渲染质量**：`Castor 已经苏醒…` 这条句子在 sandro_v1 上自"现在"起听感刺耳（对照与注入同现，指标无异常峰值）。这是渲染质量问题而非 delivery 问题；计划 §4.4 已明确 M2 不做主观质量策略，此处仅记录现象与语料，供未来质量 scope 使用。
2. **模型自带 LoRA 接口**：`VoxCPM.load_lora / set_lora_enabled / unload_lora` 存在。若未来确实需要"本地 delivery"，正当路径更像 **为表演风格训练/挂载 LoRA**，而不是往文本或提示词里塞指令。
3. **云端路线**：wire seam 可直接对接支持原生风格控制的 provider；M2 的 delivery 契约因此仍然有价值。

---

## 6. 复审要点（给独立验收）

- 机制 A 出局：4/4 形式被朗读，ASR 两遍一致（§2 表）。
- 机制 B 出局：无污染属实，但无指向性（反向指令听不出方向）+ 音色受损（§3 听判）。
- 判定过程未改变任何公共契约与运行时行为；代码差异仅注释与测试名。
- 若对 ASR 结论有异议：`candidates/delivery_probe/` 中 WAV 与 `probe.json` 均可用 §4 的命令重放。
