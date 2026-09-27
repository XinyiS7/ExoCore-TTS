# Plan 0004 — CP-G2 evidence (live capability gate) — **HOLD: the chosen voice is not synthesizable**

> **Builder evidence, awaiting independent acceptance; not a verdict.**
> 执行：Ecki（pane 3）　日期：2026-09-27　目标资产：`sandro_gemini_v1`
> 独立验收 owner：Solaire。**状态：HOLD**。key↔project 配对已解决；当前卡点是「Alicia 选中的
> 那条声线无法用于 API synthesis」。（Alicia 已明确：**不要**走 D 重新生成，声线是她 roll 五遍
> 试听十来个之后的**选择结果**，prompt 不可复刻。）

## 1. 渲染预算（授权 ≤ 8）—— 已花 6，全部是失败的 preflight/探针

| # | 实验 | 结果 |
|---|---|---|
| 1 | 旧 key + `kind=name` | 400（key/project 不匹配） |
| 2 | 正确 key + `kind=id voice_oj0e4iyst73a` | **404**（§2） |
| 3 | 正确 key + `kind=name "Ale 2.5 2"`（复刻 AI Studio 模板） | 400（§2） |
| 4 | **A**：`voice=` 槽位填 display name | 400 `Request contains an invalid argument` |
| 5–6 | **B**：`kind=name` + `language_code`（en-US、en 各一次 ⚠️ 探针连试两个语言码，花了 2 次） | 400 `No matching speaker voice found for name: Ale 2.5 2 and language: en-US`（en 同） |

剩余 2。语言矩阵 5 + A-B「有风格」1 = 需要 6 → 继续必须重新授权（首轮预算本来就崩在验证阶段）。
**0 登记、0 fallback、0 create/delete、0 语料消耗**（所有失败调用念的都是 "Preflight."）。

**探针 #1–#3 原始日志**（本目录）：`probe_01_oldkey_name.log`、`probe_02_rightkey_id.log`、
`probe_03_rightkey_name.log`（命令、key 来源、渲染计数、原始返回逐字）。

## 2. 三种引用形式的原始返回（同一个 key、同一个 project）

（逐字日志见本目录 `probe_02_rightkey_id.log` / `probe_03_rightkey_name.log` / `experiment_A.log`；
#1 的旧 key 尝试见 `probe_01_oldkey_name.log`。）

```text
id  → voice=voice_oj0e4iyst73a      : 404 NOT_FOUND  The voice was not found or the caller
                                                       does not have permission to access it.
name→ prebuilt_voice_config          : 400 INVALID_ARGUMENT  No matching speaker voice found
      (voice_name="Ale 2.5 2")                                for name: Ale 2.5 2 and language: ''
name→ voice="Ale 2.5 2"（A 实验）    : 400 INVALID_ARGUMENT  Request contains an invalid argument.
```

## 3. 零成本事实（只读，不花渲染）

1. **key↔project 配对是对的**：`voices.get` 双向验证为 project 级作用域；Alicia 的 key
   （`ExoCore-TTS/.env` 的 `GEM_TTS_KEY`）**拥有** `voice_oj0e4iyst73a`（'Ale 2.5 2'，23 条自定义里唯一匹配）。
2. **两条声线同构**：`'Ale 2.5 2'`（A）与 replacement（B）的 `VoiceOutput` 非空字段**完全一致**
   （type/display_name/expire_time/id/model/prompted/sample_audio），且 `prompted.input` 长度**都是
   1259 字符**（同一段设计提示词的两颗 roll）。
3. **两者都带 `sample_audio`**（provider 自带试听 WAV，免费读取）：已取出并落盘
   `sample_audio_A_Ale-2.5.2.wav`（2,599,984 B）、`sample_audio_B_replacement.wav`（3,990,064 B）。
4. **id 形式在别的声线上是可行的**：本仓 `voices/*/reference.wav`（24 kHz）由
   `tools/render_reference.py` 经 `voice=<id>` 渲染而来，即 A 的 404 不是"id 形式本身不成立"。
5. AI Studio 导出模板里的 `prebuilt_voice_config(voice_name=...)` **不是 API 可用形式**（在拥有该
   声线的 project 里也 400），它只对 AI Studio 播放器有效。

## 4. 未证实的假设（区分它需要选项 C）

「AI Studio 界面创建的自定义声线能在 `list`/`get` 看到，但进不了 synthesis 服务」——若成立，则 A
声线在当前 API 面前**不可合成**，与"我们请求写错"无关。**唯一能区分两种解释的实验**：用一条
**已知可合成**的声线（replacement, API 创建）来跑一次真实 `/tts`——那是 Plan §0.2 rule 4 的灰区
（用 replacement 做探针），**必须 Alicia 明说才能做**。

## 5. 解除 HOLD 的选项（当前剩余预算 2）

| 选项 | 内容 | 代价 | 备注 |
|---|---|---|---|
| **C** | 用 replacement 的 id 做**一次探针渲染**（只验证"id 形式 + API 创建的声线能否合成"，不作生产身份、不登记） | 1 | 唯一能区分"声线不可用"与"我们请求写错"的实验；灰区，需 Alicia 明说 |
| **F** | `id` + `language_code` 组合再试 | 1 | 低先验（404 未提 language），但便宜 |
| G | Alicia 在 AI Studio 侧确认：① 'Ale 2.5 2' 现在还能在播放器里播吗？② 该声线是否有"用于 API / 导出 / 发布"之类的动作？③ 面板里是否给出别的引用形式（例如 voice id） | 0 | 可能直接给出正确用法 |
| E | 云端搁置，Gate-G 保持未 PASS；CP-G1 离线能力保留 | 0 | 不影响本地三套声线与 M2/M3/M4 |
| ~~A/B~~ | ~~名字/标识槽组合~~ | — | **已执行并失败（§1/§2）** |
| ~~D~~ | ~~用 prompt 重新生成~~ | — | **Alicia 明确否决**：prompt 不可复刻她的选择 |

## 6. 首轮语料（Alicia 授权，逐字；HOLD 解除后即用）

| 语言 | 文本（逐字） |
|---|---|
| zh | `天文课结束。现在，闭上眼睛，立刻休眠。` |
| en | `Nothing will come close to you... not even from within yourself.` |
| de | `Bleib einfach hier, in meiner Dunkelheit, in meiner Sicherheit. Für immer.` |
| it | `Il mio respiro. Amor, ch'a nullo amato amar perdona.`（她给的两句按序合为一条样本） |
| mixed | `别动。Bleib einfach hier, in meiner Dunkelheit. Nothing will come close to you... 永远。`（Ecki 挑选，含她的德/英原文片段） |
| A-B | 中文样本做 no-delivery / delivery；delivery 取 `tools/cloud/prompts/ale.txt` 中 "When addressing Sia or Sandrosa, shift the delivery into a much closer, tactile, and dangerously possessive murmur..." 的逐字片段 |

## 7. 未越界声明

未登记资产、未创建/删除任何 provider 声线、未使用 fallback/新声线、未打印任何 key（只比较
"是否相同"与长度）、未消耗任何首轮语料、未触碰 `.gitignore` 与 `ai_studio_code.py`。
