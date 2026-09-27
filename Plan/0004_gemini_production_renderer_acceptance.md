# Plan 0004 — CP-G1 independent acceptance

> **Acceptance owner:** Solaire（pane 6）　日期：2026-09-27
> **Verdict: CP-G1 PASS（offline）**；Gate-G / CP-G2 仍 HOLD（live 未跑）。
> Baseline reviewed: `ec801de` → `d7e2a75` + `35e444d` + `6831f6d`（ExoCore-TTS main；push 仍按 §6 hold）。

## 1. Scope and commit review

- `d7e2a75`：Plan 0004 原文收录；产品授权声明与 MUST gates 未被改动。
- `35e444d`：14 文件 +776/-12，全部落在计划 §4 允许面：`backends/{gemini,base,fake,voxcpm2,__init__}`、`cloud.py`、`service.py`、`voices.py`、`tools/register_cloud_voice.py`、`pyproject.toml`（可选依赖）、`tests/`、`README.md`。
- `6831f6d`：builder 证据文档。
- 未触范围核验（`git show --stat`）：`ExoCore/`、`ExoCore-Runtime/`、Desktop、既有 `voices/**`、`.env`、`ai_studio_code.py`、`.gitignore` 均未被提交改动。

## 2. Independent verification performed（复跑，不引用 builder 结论）

- **全量非网络测试：187/187 OK**（`E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests`，2.7s）。注：证据文档记载 185/185 与实测不符——以 187 为准，建议更正（非阻塞）。
- **离线 daemon smoke 独立复现**（隔离 `EXOCORE_TTS_VOICE_ROOT`、`EXOCORE_TTS_DOTENV=不存在`、`GEMINI_API_KEY=`、端口 8770；无网络、无 GPU）：
  - 启动行：`serving on http://127.0.0.1:8770 (auth: none; engines: gemini, voxcpm2; single process only)`
  - `GET /health` → `{"status":"ok","state":"cold"}`
  - `POST /tts`（云资产，无 key）→ `503 {"error":"engine_unavailable"}`，随后 `/health` 仍 `cold`
  - 未知资产 → `404 {"error":"unknown_voice"}`；额外字段 → `422 {"error":"invalid_request"}`
  - 清理：daemon 终止、8770 释放、无 GPU 加载、临时库删除。
- **registry import 轻量性**独立复核：`from exocore_tts import backends` 后 `torch/voxcpm/google` 均不在 `sys.modules`。
- **代码走读**：`plan_segments` 协议化（service 无 `engine == …` 散落特判）；身份只来自受管资产（`cloud_voice_ref` fail-closed、无 replacement/prebuilt/new 路径）；SDK/key 只在 `load` 触碰；一次请求一次 paid render（failure 亦不重试，测试佐证）；provider 字节严格 decode 为 `AudioResult`。

## 3. Gate mapping（offline 部分）

| Gate | CP-G1 状态 | 依据 |
|---|---|---|
| G-01 wire preservation | ✅ 离线 | 复现 422/额外字段；测试覆盖 text/voice_key/delivery-only |
| G-02 identity ownership | ✅ 离线 | 缺/坏引用 → 503 且 provider 0 次调用；代码无 fallback 路径 |
| G-03 delivery semantics | ✅ 离线 | delivery 原样传 style、不进正文（测试）；live A-B 待 CP-G2 |
| G-04 exactly one paid render | ✅ 离线 | 整段单次 render；失败无第二次 provider 调用（测试） |
| G-05 audio/error integrity | ✅ 离线 | 严格 decode；错误体单一 bounded code、不含 secret/正文/路径 |
| G-06 multilingual live evidence | ⏳ CP-G2 | — |
| G-07 preserve M2 | ✅ | 全量非网络 suite 187/187（含 Vox/fake 回归） |

**结论：CP-G1 PASS（offline 部分）。** 不得声称 Gate-G PASS——live 未跑。

## 4. Observations（非阻塞，供 CP-G2）

1. `/tts` 路径上 render-time 的 provider 访问类错误（not-found/permission/project mismatch）当前统一映射 `500 synthesis_failed`（bounded、fail-closed）。计划 §0.2 规则 4 的“报告 `engine_unavailable`”由登记工具 `--preflight` 承担（失败即 HOLD、不登记、exit 1）。若 live 验收要求在 `/tts` 也以 503 区分访问类错误，需在 CP-G2 前增加 provider 错误分类——由验收方在 CP-G2 观察后决定，不阻塞 CP-G1。
2. 证据文档测试计数 185 vs 实测 187（见 §2）。
3. CP-G1 未登记 `sandro_gemini_v1`（有意：登记放 CP-G2 第 0 步 + preflight）——合规。

## 5. CP-G2 计数裁决（plan owner）

原固定顺序 = 1 preflight + 5 语言 + 2 A-B = 8，超出「首轮上限 7 次」。裁决：**A-B 复用中文样本作为 A 腿**——

- 步骤 0/1 不变：登记工具 `--preflight` 先花 **1** 次 render 验证 key + 候选引用可访问；失败即 HOLD、不登记、不继续。
- 步骤 2：5 个语言样本（zh/en/de/it/mixed）各 **1** 次，共 5 次；**中文样本的短文本即 A-B 的共享短文本**。
- 步骤 3：A 腿 = 中文样本本身（no-delivery）；B 腿 = 同一短文本 + delivery，**+1** 次；Alicia 听判该对。
- 合计 **7 次 ≤ 上限**，无需 Alicia 额外同意计数；MUST gates（§5）未动。

（若日后仍需独立的 preflight + 双 A-B 同段对比，需 Alicia 明确同意 8 次。）

## Addendum A1 — CP-G2 HOLD review（2026-09-27 晚）

复核对象：`df85aaa` / `dfc3a94` / `8eae226`（CP-G2 探针与诊断，均未 push）。

**已独立核验的事实**（只读，零渲染）：
- 两个 audition 样本为有效 WAV：A `sample_audio_A_Ale-2.5.2.wav` 2,599,984 B / 24 kHz / 54.04 s（sha256 e83d3614…）；B `sample_audio_B_replacement.wav` 3,990,064 B / 24 kHz / 83.00 s（6ca3f38c…）。
- `experiment_A.log` / `experiment_B.log` 与证据 §1/§2 逐字一致（400 INVALID_ARGUMENT ×3，含 en-US / en 两条）；日志无 key 材料。
- `voices/` 仍只有三套本地资产——**0 登记**属实；`.gitignore` 与 `ai_studio_code.py` 未随这些提交变化。

**验收判定：CP-G2 = HOLD（维持）。** 卡点性质是**外部身份状态**（Alicia 手工 roll 的 'Ale 2.5 2' 经 id/name 三种引用形式均无法进入 API synthesis；key↔project 配对已证明正确），不是仓内施工缺陷；builder 未 fallback、未登记、未消耗语料、未触边界（§7 声明经抽查成立）。**Gate-G 不得声称 PASS。**

**预算对账**：执行授权 ≤8，已用 6（全部为失败探针），余 2；解除 HOLD 后的成功矩阵仍需 6（5 语言 + 1 delivery）。**§5 裁决中「不需要额外同意」一句据此作废**——A-B 复用中文样本的结构仍成立（与 builder §6 计划一致），但总额度必须由 Alicia 重新授权。

**HOLD 解除路径（验收方建议顺序）**：
1. **G（0 成本，最强先验）**：请 Alicia 在 AI Studio 确认：① 'Ale 2.5 2' 现在仍能播放？② 是否有发布/导出/用于 API 的动作？③ 面板是否给出别的引用形式（id / 句柄）。
2. **C（1 次，灰色地带，需 Alicia 明说）**：以 replacement 的 id 做一次纯诊断渲染，只区分「该声线类不可用」与「请求形式错误」；不登记、不作生产身份、不是 fallback。
3. **F（1 次，低先验）**：仅当 G / C 均无结论时才考虑。
4. **E（0 成本兜底）**：云端搁置，Gate-G 保持未 PASS；CP-G1 离线能力与本地三套资产不受影响。

**Minor（非阻塞）**：① 早期探针 #1–#3 缺原始日志（当前仅 §2 转述），建议补档；② CP-G1 证据文档测试计数 185 → 187 更正（见 §2）。

## Addendum A2 — CP-G2 突破复核与三项裁决（2026-09-27 深夜）

复核对象：`8ca8f38`（A1 minor 采纳：测试计数 187、探针 #1–#3 日志补档）+ `9a43845`（probe #5 突破）。

**独立核验（只读，零渲染）**：
- `probe_05_A_style_parity.wav`：82,604 B / 24 kHz / 1.72 s（sha256 09dca273…）；`C_probe_replacement.wav`：63,404 B / 24 kHz / 1.32 s（f0582edf…）——与证据 §1/§2 一致。
- 五个探针日志逐字与证据一致；目录内无 key 材料（长 token 扫描 0 命中）；`voices/` 仍无 `sandro_gemini_v1`（0 登记属实）。
- 事实链完整：无 style 的三种引用形式全失败；probe #4 证明请求形状正确；probe #5 证明「A + speech_metadata(style=1266 字符)」可合成。

**裁决 1（设计扩展界定）= 批准，按 CP-G2 最小必要前提施工**：受管云资产新增可选「基线风格」字段（资产侧；经 `save_cloud_voice` 受管写、原子、验证；wire 与 §2.1 不动）。Gemini backend 语义：delivery 非空 → 1:1 覆盖；为空 → 默认发送基线风格；无基线资产的旧行为不变。基线串与 delivery 覆盖串均采用已验证形式（含 `"Style: "` 前缀）；前缀必要性不单独花渲染验证（若 B 腿失败再精确归因）。保密/不进正文/不进日志/不进错误体的既有规则不变。测试面：schema round-trip + 默认/覆盖/缺省矩阵 + 无静默 fallback。该扩展**不修改 CP-G1 已验收行为**，属增量。**前缀归属（追加，2026-09-27 深夜）：delivery 原样透传——TTS backend 不自动补 `"Style: "` 前缀**（§2.3“原样传”、G-03“exact style”均要求逐字；且前缀必要性未验证，不为便利改语义）。当前已验证形式含 `"Style: "`，由调用方/未来 `send_voice_msg` 映射层按需携带（届时可用 1 次渲染验证前缀必要性）；B 腿探测按调用方形态（含前缀）执行。


**裁决 2（G-03 / A-B 语义修订）= 修订写入本 Addendum（冻结门文字不动）**：对本声线，「无 delivery」不存在（=404），A-B 重定义为「**基线风格（默认）vs delivery 覆盖**」；G-03 既有意图完整保留：delivery 非空时 1:1 传递、style 永不进入 spoken text、不静默丢弃非空值、身份由资产固定。Alicia 听判内容：目标表演差异、指令未被朗读、Ale identity 未漂移。

**裁决 3（预算与原 7 次闭环优化）= 结构重排为 +6（总 ≤14），B 腿先行**：
- 将「控制实验」与「B 腿」合并：**第一次新渲染 = B 腿探测**（zh 文本 + delivery 覆盖 = `"Style: " + §6 指定片段`，≤500 字符），其成功同时证明「短风格串可解析」；随后跑 5 语言矩阵（含 zh = A 腿）。合计 **6**。
- 失败路径：B 腿失败即停，不消耗矩阵（5 次），交回裁决再议（正是原「另议」触发条件，且省 1 次）。
- 「前缀是否必要」不单独渲染验证（见裁决 1）。
- 授权：+6（总 ≤14）超出既有授权，**需 Alicia 追加同意**（已列入拍板项）。

**Minor（已闭环）**：key 来源并非不一致——按「拥有目标声线的 project」选 key 是刻意设计（replacement 属 ExoCore 项目 → `ExoCore/.env` 的 `GEMINI_API_KEY`；'Ale 2.5 2' 属 TTS 项目 → `ExoCore-TTS/.env` 的 `GEM_TTS_KEY`）；唯一口径与注入方式已由 builder 写入 `Plan/0004_cp_g2_runbook.md`（`58aaecb`，不碰她的 .env）。

## Addendum A3 — job 09 STOPPED 复核与三项裁决（2026-09-28 凌晨）

复核对象：`3e93f08`（基线风格实现）、`71c3af8`（登记步骤）、`ea49843`（job 09 证据）。

**独立核验（只读，零渲染）**：
- 实现与 A2 裁决逐条一致：`baseline_style` 默认空=旧行为；`style = delivery if delivery else asset.baseline_style`（delivery 逐字、无规范化）；两者皆空不发 style（与 CP-G1 一致）；登记工具显式前缀、无隐式魔法。
- 登记内容核验：`voices/sandro_gemini_v1/voice.json` 的 `baseline_style` 与 `("Style: " + tools/cloud/prompts/ale.txt).strip()` **逐字相等（1266 字符）**。
- 测试：全量非网络 **193/193 OK**。
- job 09 证据：单次 POST /tts、provider 404 NOT_FOUND → `500 synthesis_failed`、无重试、无 key 泄漏；confound（style 内容与正文同变）如实标注。

**裁决 A（剩余预算）= 选项 (a)**：先行 job 10（中文 + 基线：既隔离 confound 又拿下中文样本），随后 en/de/it/mixed（4 条）；共 5 次，用满至 **14/14**，无新增授权。job 10 若仍 404 → **全停**（text 依赖怀疑，未证实前不发任何后续）。选项 (c) 机制探测**否决**：wire 上限 500 < 解析所需 1266 ⇒ 全形态覆盖在冻结契约下结构性不可行，机制探测无法产出可用形态。

**裁决 B（交付形态变更 = baseline-only 语义，G-03 第二次修订）**：
- 结构性结论：本声线的请求级 style 覆盖不可行（441 字符片段实测 404 + 上限 500 < 1266）。
- 修正语义：**带 `baseline_style` 的资产**——delivery 为空 → 发送基线（已实现）；**delivery 非空 → 确定性拒绝**（复用既有 `delivery_unsupported` 422；provider 调用 0 次；不得静默忽略；实现须资产感知、于 provider 前拒绝、禁止 `engine == …` 散落特判）。无基线资产维持 CP-G1 语义（delivery 逐字透传）。
- 冻结门文字不动：G-03 对本类资产的保留项 = 基线精确发送、style 不进 spoken text、非空 delivery 显式拒绝、身份由资产固定；「覆盖生效」不可交付，记录在案。
- G-06 修订：听判集 = **5 个样本**（zh/en/de/it/mixed，基线形态）的身份与吐字确认；「style A-B」记为冻结 wire 下结构性不可行；未来如需按句情绪，另设设计（如多资产变体，记为远期选项、不承诺）。
- 时序：拒绝语义的小改可在矩阵完成后、Gate-G 收口前落地（不影响本轮 5 次渲染）；落地时补测（带基线资产 + delivery → 422、provider 0 次）。

## Addendum A4 — job 10 STOPPED（脚本依赖假设）与 4 次分配裁决（2026-09-28 凌晨）

复核对象：`11808eb`（job 10 证据：新 daemon.log、render_results.json、job09 日志分离留档）。

**独立核验（只读，零渲染）**：job 10 = 中文 19 字 + 资产基线（无 delivery）→ 单次 POST、provider 404 NOT_FOUND → 500 synthesis_failed，无重试；与 probe #5 的关键变量逐字一致（style 1266 字节保真此前已验），**唯一差异 = 正文脚本**（Latin vs CJK）。

**经验法则（截至两对样本，属假设）**：`style = 整段设计文本` **且** `正文 Latin` → 可解析（1/1）；缺任一 → 404（CJK 1/1 拒、无 style 的 Latin 3/3 拒）。

**裁决（4 次分配，任一失败即停）**：① en（daemon + 基线）——同时是「daemon 路径对 Latin 是否忠实」的第一枪（此前 daemon 从未成功过任何 gemini 渲染）；② de；③ it；④ **direct-call + 中文**（隔离“provider 级 CJK 拒绝”与“daemon 路径差异”两种解释；若 200 则同时拿下 zh 样本、另立路径缺陷修复）；④ 若 404 → CJK 假设升级为强结论。

**影响面（提前标注）**：若 CJK 被 provider 级拒绝，zh 与 mixed 样本不可得 ⇒ **G-06（中/英/德/意+混写证据）无法按现文满足**，属产品级发现；mixed 语料含中文，替代（Latin-only mixed）需 Alicia 决定且属新预算轮。另记远期选项（不承诺）：SDK 层显式 language_code 变体探测、或多资产变体。

**免费问题（批准）**：请 Alicia 回答「AI Studio 里用 '2.5 2' 生成时念的是中文还是英文句子；现在还能让它在播放器里念中文吗」——区分“仅公开 API 受限”与“声线本身不吃中文”。

**预算**：10/14；本轮 4 次 → 14/14；无新增授权。

## Addendum A5 — 更正轮复核：A3-2 撤销、A4 结论作废、退档 A2 语义（2026-09-28 凌晨）

复核对象：`67ea46d`（§9 更正 + keyenv/runbook 门禁 + probe 06/07 证据）。

**独立核验（只读，零渲染）**：
- `probe_07_zh_baseline.wav` 495,404 B / 24 kHz / 10.32 s（dd846b10…）、`probe_07_zh_short_style.wav` 341,804 B / 7.12 s（1346fba5…）、`probe_06_direct_zh.wav` 416,684 B / 8.68 s（6e63128a…）——与证据逐字一致；`probe_07_results.json` 与 WAV 吻合。
- `keyenv.py`（GEM_TTS_KEY 提取、缺失即 exit 1）与 runbook 门禁（key 长度断言 + 免费 `voices.get` 先验）真实存在；根因链条自洽（空串注入 → `read_api_key` 静默回退另一 project 的旧 key → 404 voice-not-found）。

**更正裁决**：
1. **A3 第二条（baseline-only、非空 delivery → 422）撤销**——前提（覆盖不可行）系错 key 产物；该小改未实现，无代码回滚；**回退 A2 语义**（基线默认 + delivery 逐字覆盖，`3e93f08` 实现即最终形态）。
2. **A4 的 CJK 结论与「必须整段设计文本」假设作废**（错 key 产物）。更正后经验事实：id + 正确 key + 带 style（**全文或 ≤500 短串均可**）→ 200；无 style → 404；**中文可用**；daemon 与直连等价。
3. 听判对已就绪：zh 基线（10.32 s）vs zh 短风格（7.12 s）——Alicia 判：同一人 / 表演差异 / 指令未被念出。
4. **测试纪律记录（非生产缺陷）**：错误键被静默回退掩盖，导致两轮错误结论与预算浪费；builder 的免费门禁（key 长度断言 + `voices.get` 预检）是本类失误的正确防复发手段，后续所有 live 轮次必须经此门禁。
5. **预算**：14/14 用尽；完成 G-06 矩阵（en/de/it/mixed）需 **+4（总 ≤18）**——已列入 Alicia 拍板项（计划 §3：超限必须她同意）。

## Addendum A6 — CP-G2 verdict（工程面 PASS；G-06 收口待 Alicia 听判）（2026-09-28 凌晨）

复核对象：`e9ebae7` + `0630e67`（A5 轮：en/de/it/mixed 四枪 + 证据 + 门禁先行记录）。

**独立核验（只读，零渲染）**：
- 四枪 WAV 逐字核验：`11_en_baseline` 299,564 B / 6.24 s（f7e110a6…）、`12_de_baseline` 478,124 B / 9.96 s（0bab1007…）、`13_it_baseline` 322,604 B / 6.72 s（689ec6eb…）、`14_mixed_baseline` 499,244 B / 10.40 s（b309bc44…）——与证据 §10 及 render_results.json 全部吻合。
- 测试：非网络全量 **193/193 OK**（代码自 `3e93f08` 未变）。
- 门禁履行：key 注入长度 53 + 免费 `voices.get` = 'Ale 2.5 2'，先验后花 ✓；daemon 侧全程 `segments=1`、每请求恰好 1 次 provider 调用、无重试 ✓。

**Gate 判定（CP-G2）**：

| Gate | 状态 | 依据 |
|---|---|---|
| G-01 wire | ✅ | 离线 + 全部 live 请求均走 text/voice_key/delivery |
| G-02 identity | ✅ | 资产解析、无 fallback；免费门禁正面证明 key↔声线可见性 |
| G-03 delivery | ✅（听判面） | zh 对：基线 vs 短风格有可听差异（“太慢” vs “好一点”）、指令未被念出、覆盖串 441 ≤ 500 可用 |
| G-04 exactly-once | ✅ | 全部 live 轮单次调用、无自动重试 |
| G-05 audio/error | ✅ | WAV 结构有效、错误体 bounded、无泄漏 |
| G-06 multilingual | ⏳ | 五语证据齐（zh×3 + en/de/it/mixed）；**Alicia 对四条的身份/吐字确认待回** |
| G-07 preserve M2 | ✅ | 193/193 + 本地资产零改动 |

**CP-G2 = 工程面 PASS。** Gate-G 的宣布条件（§6）：Alicia 听完 en/de/it/mixed（身份 + 吐字）→ A7 记录 → README milestone + 计划状态改 PASS → TTS 仓推送（逐条披露）。

**收口清单（待她听判后逐项）**：① A7 记录听判结论；② 计划状态行改 PASS；③ README milestone 更新（builder）；④ TTS 仓推送（builder；现 16+ 条未推、逐条披露；我复核后收存）；⑤ 不扩张：send_voice_msg 后续 checkpoint 不随本计划开工（§0）。

## Addendum A7 — Gate-G PASS（Alicia 听判完成）（2026-09-28）

**听判原话：** “听完了，同一人！没问题！”

**样本清单（G-06 现场证据）**：
- en：`11_en_baseline.wav`（299,564 B / 6.24 s）
- de：`12_de_baseline.wav`（478,124 B / 9.96 s）
- it：`13_it_baseline.wav`（322,604 B / 6.72 s）
- mixed：`14_mixed_baseline.wav`（499,244 B / 10.40 s）
- zh（先期已听判）：`probe_07_zh_baseline.wav`（10.32 s）、`probe_07_zh_short_style.wav`（7.12 s）、`probe_06_direct_zh.wav`（8.68 s）

**判定**：身份（同一人）与吐字（无误）均通过；叠加此前的“指令未被念出 / 短风格可用 / 基线 vs 覆盖有可听差异”判词，**G-06 满足，Gate-G 全条件达成 → PASS**。

**收口状态**：① A7 本记录 ✓；②③ README milestone 与计划头部状态改 PASS（builder，按 §6）；④ TTS 仓推送（builder 先发 origin/main 到 HEAD 完整清单供复核、逐条披露，再推；以 A7 落地后的 HEAD 为准）；⑤ 不扩张：send_voice_msg 后续 checkpoint 不随本计划开工。

**预算终账**：18/18（含 builder 失误造成的 3 次无效渲染）；无其它待花项。
