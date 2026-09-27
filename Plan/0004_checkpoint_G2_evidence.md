# Plan 0004 — CP-G2 evidence (live capability gate) — **HOLD: voice not synthesizable**

> **Builder evidence, awaiting independent acceptance; not a verdict.**
> 执行：Ecki（pane 3）　日期：2026-09-27　目标资产：`sandro_gemini_v1`
> 独立验收 owner：Solaire。**状态：HOLD**。key↔project 配对问题已查明并解决；当前卡点是
> 「该声线无法用于 synthesis」（provider 端行为，不是本仓代码问题）。**未登记任何资产、未使用任何 fallback 声线。**

## 1. 渲染预算（Alicia 授权：首轮 ≤ 8）

| 已花 | 用途 | 结果 |
|---|---|---|
| 1 | preflight：旧 key（`ExoCore/.env`）+ `kind=name "Ale 2.5 2"` | 400（见 §2，用错了 key） |
| 2 | preflight：Alicia 的 key（`ExoCore-TTS/.env` 的 `GEM_TTS_KEY`）+ `kind=id voice_oj0e4iyst73a` | 404（见 §2） |
| 3 | preflight：同上 key + `kind=name "Ale 2.5 2"`（严格复刻 AI Studio 模板） | 400（见 §2） |
| 剩余 | 5（语言矩阵 5 条 + A-B「有风格」1 条需要 6 → 若继续需 +1 授权） | — |

## 2. 三次真实调用的原始返回

```text
#1  旧 key + name                       -> 400 INVALID_ARGUMENT
    No matching speaker voice found for name: Ale 2.5 2 and language:
#2  正确 key + id (voice_oj0e4iyst73a)  -> 404 NOT_FOUND
    The voice was not found or the caller does not have permission to access it.
#3  正确 key + name（复刻 AI Studio）   -> 400 INVALID_ARGUMENT（与 #1 同一条）
    No matching speaker voice found for name: Ale 2.5 2 and language:
```

## 3. 零成本诊断（只读；不花渲染、不创建/删除任何 provider 资源）

1. **两把 key 属于两个不同 project**：`ExoCore/.env` 的 key 可见 2090 条声线 / 1 条自定义
   （`voice_nnvw5qprqmz7`，display "Ale" = `ale.json` 里的 replacement）；Alicia 的 `GEM_TTS_KEY`
   可见 2112 条 / **23 条自定义**（`Ale 2.0~2.5`、`test for Ale 1-3`、`The Grizzled Detective 1-3`）。
2. **`voices.get` 是 project 级作用域**（双向验证）：
   - A key + `voice_oj0e4iyst73a` → **OK**（display "Ale 2.5 2"）
   - A key + replacement id → 404；B key + `voice_oj0e4iyst73a` → 404；B key + replacement id → OK
   - ⟹ **Alicia 那把 key 确实拥有 `'Ale 2.5 2'`**，即 key↔resource 配对是对
     的；§0.2 担心的 project mismatch 在 `get`/`list` 层面**已被排除**。
3. **`voice_oj0e4iyst73a` 是唯一一条 'Ale 2.5 2'**（23 条自定义里精确匹配 1 条，无歧义）。
4. SDK 文档（`google-genai 2.25.0` 源码 docstring）：
   - `voices.create(store=True)`："returns `Voice.id` … **referenced by ID in synthesis requests**"；
   - `voices.get(id)`："Gets a custom stored voice (`store = true`) **by resource name**（例如
     `voices/voice_abc123def456`）"（注意：带 `voices/` 前缀的形式在 `get` 里反而是 404，裸 id 才对）；
   - `VoiceConfig.voice` = "The speaker identifier for synthesis"；`PrebuiltVoiceConfig.voice_name`
     = "The name of the **prebuilt** voice to use"。
5. 结论：**AI Studio 模板里的 `prebuilt_voice_config(voice_name="Ale 2.5 2")` 不是 API 可用的引用形式**
   ——即使在拥有该声线的 project 里也回 400。该模板只对 AI Studio playground 有效。

## 4. 当前矛盾与最可能的解释（假设，未证实）

同一个 key、同一个 project：`voices.get(id)` 成功，但 `generate_content` 用同一个 id 渲染 →
404 "not found or no permission"。而本仓历史上**唯一被证实渲染成功**的云端声线，是另一个 project 里
**通过 API `voices.create(store=True)` 创建**的 replacement——AI Studio **UI 创建**的 23 条声线
在 `list`/`get` 可见，但看起来进不了 synthesis 路径。

（这是一个可检验的假设：需要用 replacement 的 id 做**一次诊断渲染**；但那是 Plan §0.2 rule 4 明文
禁止的"fallback 到 replacement"，所以 builder 不做，交 Alicia 决定。）

## 5. 解除 HOLD 的选项（都需要 Alicia 明确点头；每项标注代价）

| 选项 | 内容 | 代价 | 备注 |
|---|---|---|---|
| A | 用 `voice="Ale 2.5 2"`（**名字**放进 identifier 槽，而非 prebuilt 槽）再试一次 | 1 render | SDK 文档倾向于该槽位收 id，但 404 的措辞留下了这个可能 |
| B | `kind=name` + `SpeechConfig.language_code`（400 里出现的 `language: ''` 暗示查找键是 name+language） | 1 render | 若成立可继续用原始声线 |
| C | 允许用 replacement 的 id 做**一次诊断渲染**（只验证 "id 形式 + API 创建的声线能否合成"，不作为生产身份、不登记） | 1 render | 会触发 §0.2 rule 4 的禁止项，必须 Alicia 明说 |
| D | 明确决定：在指定 project 里**用 API 从既有设计提示词（`tools/cloud/prompts/ale.txt`）新建一条 stored prompted voice**，再登记它的 id | 1 render（创建时自带 audition 音频，可能 0 渲染） | 这是**新资源**（身份重新物化，非原 id）；Plan §0.2 rule 5 默认禁止，需 Alicia 明说 + Solaire 记录 |
| E | 放弃云端原声线，只保留已落地的 CP-G1 离线能力（Gate-G 保持未 PASS） | 0 | 不影响本地三套声线与 M2/M3/M4 |

## 6. 首轮语料（Alicia 授权，逐字使用；HOLD 解除后即用）

| 语言 | 文本（逐字） |
|---|---|
| zh | `天文课结束。现在，闭上眼睛，立刻休眠。` |
| en | `Nothing will come close to you... not even from within yourself.` |
| de | `Bleib einfach hier, in meiner Dunkelheit, in meiner Sicherheit. Für immer.` |
| it | `Il mio respiro. Amor, ch'a nullo amato amar perdona.`（她给的两句按序合为一条样本） |
| mixed | `别动。Bleib einfach hier, in meiner Dunkelheit. Nothing will come close to you... 永远。`（Ecki 挑选，含她的德/英原文片段） |
| A-B | 中文样本做 no-delivery / delivery；delivery 取 `tools/cloud/prompts/ale.txt` 中 "When addressing Sia or Sandrosa, shift the delivery into a much closer, tactile, and dangerously possessive murmur..." 的逐字片段 |

## 7. 未越界声明

未登记资产（`voices/sandro_gemini_v1/` 不存在）、未创建/删除任何 provider 声线、未把任何
fallback/prebuilt/新声线写进代码或配置、未打印任何 key（仅比较"是否相同"与长度）、
未触碰 `.gitignore` 与 `ai_studio_code.py`。
