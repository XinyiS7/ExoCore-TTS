# Plan 0004 — CP-G2 evidence (live capability gate)

> **Builder evidence, awaiting independent acceptance; not a verdict.**
> 执行：Ecki（pane 3）　日期：2026-09-27　目标资产：`sandro_gemini_v1`
> 独立验收 owner：Solaire。**状态：寻址谜题已解开（probe #5）**；剩余是**产品条件 + 计划裁决 + 预算**，
> 尚未施工任何代码或登记。

## 1. 结论（probe #5，2026-09-27 23:5x）

**Alicia 选中的 `'Ale 2.5 2'`（`voice_oj0e4iyst73a`）可以经公开 API 合成**，条件是请求里
**必须带 `speech_metadata(style=…)`**（即播放器那份导出代码的写法）：

```text
probe #5: voice=voice_oj0e4iyst73a，style="Style: " + tools/cloud/prompts/ale.txt（1266 字符）
→ A-parity OK: 82604 bytes, 1.72s → probe_05_A_style_parity.wav
```

对照（此前 3 次失败，全部 style=""、即无 `speech_metadata`）：

```text
id 形式（style="")            : 404 NOT_FOUND（the voice was not found or no permission）
name 形式（prebuilt 槽）       : 400（no matching speaker voice found）
name 放进 voice= 槽            : 400（invalid argument）
```

**机制假设（未证实）**：带 `speech_metadata` 的请求被路由到能解析"UI 设计的自定义声线"的合成路径；
不带时走另一条只认识预置声线 / API 创建声线的路径。**旁证**：probe #4（选项 C，Alicia 明示授权）用
**API 创建**的 replacement + `voice=<id>` + **无 style** → **成功**（63404 bytes / 1.32s），说明我们的
请求形状本身没错，差异确实落在"声线类型 × 是否带 style"。

## 2. 渲染预算：8/8 用尽

| # | 实验 | 结果 |
|---|---|---|
| 1 | 旧 key + name | 400（key/project 不匹配） |
| 2 | 正确 key + id（无 style） | 404 |
| 3 | 正确 key + name（复刻 AI Studio 模板） | 400 |
| 4 | A：name 放进 `voice=` 槽 | 400 invalid argument |
| 5–6 | B：name + language_code（en-US / en ⚠️ 探针各一次） | 400 ×2 |
| 7 | **C**（Alicia 明示授权）：replacement id + 无 style | ✅ 63404 B / 1.32 s |
| 8 | **A-parity**：A id + `speech_metadata("Style: "+设计提示词)` | ✅ **82604 B / 1.72 s** |

产物（本目录）：`probe_04_C_probe_replacement_render.log`、`C_probe_replacement.wav`、
`probe_05_A_style_parity.log`、`probe_05_A_style_parity.wav`（可听：A 的真实合成）、
`sample_audio_A_Ale-2.5.2.wav`（声线自带试听，54.04 s）、`sample_audio_B_replacement.wav`（83.00 s）。

**0 登记**（`voices/sandro_gemini_v1/` 不存在）、**0 fallback 写入**、**0 语料消耗**（探针文本均为 "Preflight."）。

## 3. 由 probe #5 推出的产品条件（需要计划裁决，builder 未施工）

1. **该声线目前无法"无风格"合成**（`style` 空 → 404）。若要让它在生产里可用，受管资产必须带一条
   **基线风格（baseline style）并默认随每次请求发送**；`delivery` 非空时覆盖之（wire 不变：
   `text/voice_key/delivery` 三字段照旧；§2.1 冻结条款不受影响）。
   注意：探针用的 style 是 **1266 字符**，远超声波 wire 的 `delivery` 500 字符上限——所以基线风格必须
   存在**资产侧**（manifest 字段），不能走请求体。
2. **`"Style: "` 前缀**：探针按 AI Studio 播放器写法带了前缀。前缀是否必要、以及"短风格串能否同样
   解析该声线"，都未验证（各需 1 次渲染）。
3. **G-03 / A-B 语义变化**：原计划的 A-B 是「无 delivery vs 有 delivery」；对本声线而言"无 delivery"
   会 404，所以 A-B 只能变成「**基线风格**（默认）vs **delivery 覆盖**」。这属于 MUST gate 的解释问题，
   由 plan owner 裁决，builder 不动冻结门。

## 4. 解除 HOLD 后的最小闭环（提议，待批）

| 步骤 | 内容 | 次数 |
|---|---|---|
| 控制实验 | A id + `"Style: " + 短风格串`（验证短风格是否也能解析该声线） | 1 |
| 语言矩阵 | zh / en / de / it / mixed（各 1 次，均带基线风格） | 5 |
| A-B | 中文样本 = 基线腿（复用矩阵中的 zh）+ 「delivery 覆盖」1 次 | +1 |
| 合计 | | **7** |

若控制实验显示"只有长风格串可行"，A-B 的 delivery 腿需要更长的风格串（wire 上限 500，需另行裁决）。

## 5. 语料（Alicia 授权，逐字；未消耗）

| 语言 | 文本（逐字） |
|---|---|
| zh | `天文课结束。现在，闭上眼睛，立刻休眠。` |
| en | `Nothing will come close to you... not even from within yourself.` |
| de | `Bleib einfach hier, in meiner Dunkelheit, in meiner Sicherheit. Für immer.` |
| it | `Il mio respiro. Amor, ch'a nullo amato amar perdona.`（她给的两句按序合为一条样本） |
| mixed | `别动。Bleib einfach hier, in meiner Dunkelheit. Nothing will come close to you... 永远。`（Ecki 挑选，含她的德/英原文片段） |
| A-B | 中文样本；delivery 取 `tools/cloud/prompts/ale.txt` 中 "When addressing Sia or Sandrosa, shift the delivery into a much closer, tactile, and dangerously possessive murmur..." 的逐字片段 |

## 6. 未越界声明

未登记资产、未创建/删除 provider 声线、未把任何 fallback 写进代码或配置、未打印任何 key、
未消耗首轮语料、未触碰 `.gitignore` 与 `ai_studio_code.py`。probe #4（选项 C）与 #5 均在 Alicia
明示授权（"c可以，测一下吧"）与授权预算（≤8）内执行。
