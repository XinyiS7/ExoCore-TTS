# Plan 0004 — CP-G2 evidence (live capability gate) — **HOLD at preflight**

> **Builder evidence, awaiting independent acceptance; not a verdict.**
> 执行：Ecki（pane 3）　日期：2026-09-27　目标资产：`sandro_gemini_v1`（原始 Ale voice resource）
> 独立验收 owner：Solaire。**当前状态：HOLD（project-bound identity 未配对）**，按 Plan §0.2 / §3 停在第一个失败点。

## 1. 渲染预算（Alicia 授权：首轮 ≤ 8 次 provider render）

| # | 用途 | 状态 |
|---|---|---|
| 1 | preflight（当前 key + `{"kind":"name","value":"Ale 2.5 2"}`） | **已花（失败）** |
| 2–8 | 剩余（未花）| — |

若后续配对成功，最小可闭环路径（总花费正好 8）：preflight#2（1）+ 语言矩阵 5（zh/en/de/it/mixed）+ A-B 只补「有风格」一条（1，基线复用中文样本）。超过 8 或失败后批量重跑，先重新征得 Alicia 同意。

## 2. preflight 尝试 #1（唯一一次真实调用）

命令（key 只走环境/`.env`，未打印）：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/register_cloud_voice.py \
    --key sandro_gemini_v1 --kind name --value "Ale 2.5 2" --preflight
```

provider 返回（原样）：

```text
FAIL preflight: ClientError: 400 INVALID_ARGUMENT.
{'error': {'code': 400, 'message': 'No matching speaker voice found for name: Ale 2.5 2 and language: ', 'status': 'INVALID_ARGUMENT'}}
```

事实读数：**请求到达了 API**（不是鉴权失败）——当前 key 有效；被拒的是「按名字解析声线」这一步。
`--preflight` 失败即中止，**未登记任何资产**（`voices/sandro_gemini_v1/` 不存在），未创建/删除任何
provider voice，未使用 fallback。

## 3. 只读诊断（零成本，不产生任何 provider render）

用同一把 key 读声线清单（`client.voices.list()`，翻页至尽）：

- 账号可见声线总数 **2090**（几乎全是 `type=prebuilt` 的多语言预置声线）；
- `type=prompted`（自定义）声线**恰好 1 个**：
  - `id = voice_nnvw5qprqmz7`，`display_name = "Ale"`，`model = models/gemini-3.8-flash-tts`，
    `expire_time = 2027-09-26 16:15:06Z`；
- 该记录与仓库里 `tools/cloud/voices/ale.json` **逐字段一致**——也就是说：当前 key 所属 project 里
  存在的自定义声线就是 Plan §0.1 所说的 **replacement**，而不是 Alicia 所指的原始 Ale；
- 清单里**不存在**任何名字为 `Ale 2.5 2` 的声线（该名字来自 `ai_studio_code.py` 的
  `prebuilt_voice_config(voice_name=...)`）。

## 4. 判定与处置（按冻结规则，无自行动作）

这与 Plan §0.1/§0.2 预判的情况完全吻合：**Gemini prompted voice 与创建它的 project/key 绑定**，
只换 key 或只换名字都不足以证明配对。因此：

- 不 fallback 到 replacement、prebuilt 或任意新声线（Plan §0.2 rule 4）；
- 不调用 `create_voice()`、不重建（rule 5）；
- 不把「名字解析失败」自行改写成「换个槽位再试一次」（那是第二次付费尝试 + 身份猜测）；
- 停在 HOLD，交回 Alicia。

## 5. 解除 HOLD 需要（Alicia 二选一）

1. **把原始 Ale 所在 AI Studio project 的 key 放到仓库外**（环境变量或 `ExoCore/.env`；她说过该 key
   专门用于生成 Gemini TTS 声音）。key 到位后我可以用零成本的 `voices.list()` 在该 project 下找出
   原始声线的 `voice_xxx` id（再请她确认身份），然后花 1 次 render 重跑 preflight；
2. 或者她直接给出**原始 voice resource id**（`voice_xxx` 形式），与 key 必须同 project。

若她**主动改变主意**、要拿 replacement 当生产身份：那是对 Plan 冻结身份的替换，必须由她明说 +
plan owner（Solaire）记录，`ale.json` 里已有的 `voice_nnvw5qprqmz7` 才能进入登记（builder 不自行决定）。

## 6. 首轮语料（Alicia 授权，逐字使用；用于解除 HOLD 后的语言矩阵）

| 语言 | 文本（逐字） |
|---|---|
| zh | `天文课结束。现在，闭上眼睛，立刻休眠。` |
| en | `Nothing will come close to you... not even from within yourself.` |
| de | `Bleib einfach hier, in meiner Dunkelheit, in meiner Sicherheit. Für immer.` |
| it | `Il mio respiro. Amor, ch'a nullo amato amar perdona.`（她给的两句按序合为一条样本） |
| mixed | `别动。Bleib einfach hier, in meiner Dunkelheit. Nothing will come close to you... 永远。`（混合样本由 Ecki 挑选，含她的德/英原文片段；如有异议可重挑） |
| A-B | 同一条短文本（中文样本）做 no-delivery / delivery；delivery 取 `tools/cloud/prompts/ale.txt` 里 "When addressing Sia or Sandrosa, shift the delivery into a much closer, tactile, and dangerously possessive murmur..." 的**逐字片段** |

（**未执行**：以上语料尚未产生任何调用；本文件只为把授权记录在案。）
