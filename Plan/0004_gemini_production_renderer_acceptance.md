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

**裁决 1（设计扩展界定）= 批准，按 CP-G2 最小必要前提施工**：受管云资产新增可选「基线风格」字段（资产侧；经 `save_cloud_voice` 受管写、原子、验证；wire 与 §2.1 不动）。Gemini backend 语义：delivery 非空 → 1:1 覆盖；为空 → 默认发送基线风格；无基线资产的旧行为不变。基线串与 delivery 覆盖串均采用已验证形式（含 `"Style: "` 前缀）；前缀必要性不单独花渲染验证（若 B 腿失败再精确归因）。保密/不进正文/不进日志/不进错误体的既有规则不变。测试面：schema round-trip + 默认/覆盖/缺省矩阵 + 无静默 fallback。该扩展**不修改 CP-G1 已验收行为**，属增量。

**裁决 2（G-03 / A-B 语义修订）= 修订写入本 Addendum（冻结门文字不动）**：对本声线，「无 delivery」不存在（=404），A-B 重定义为「**基线风格（默认）vs delivery 覆盖**」；G-03 既有意图完整保留：delivery 非空时 1:1 传递、style 永不进入 spoken text、不静默丢弃非空值、身份由资产固定。Alicia 听判内容：目标表演差异、指令未被朗读、Ale identity 未漂移。

**裁决 3（预算与原 7 次闭环优化）= 结构重排为 +6（总 ≤14），B 腿先行**：
- 将「控制实验」与「B 腿」合并：**第一次新渲染 = B 腿探测**（zh 文本 + delivery 覆盖 = `"Style: " + §6 指定片段`，≤500 字符），其成功同时证明「短风格串可解析」；随后跑 5 语言矩阵（含 zh = A 腿）。合计 **6**。
- 失败路径：B 腿失败即停，不消耗矩阵（5 次），交回裁决再议（正是原「另议」触发条件，且省 1 次）。
- 「前缀是否必要」不单独渲染验证（见裁决 1）。
- 授权：+6（总 ≤14）超出既有授权，**需 Alicia 追加同意**（已列入拍板项）。

**Minor**：key 来源在探针记录间不一致（probe #4 记 `ExoCore/.env`，前文记 `ExoCore-TTS/.env` 的 `GEM_TTS_KEY`）——CP-G2 正式执行前请固定唯一来源并写入 runbook。
