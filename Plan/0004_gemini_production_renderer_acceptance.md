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
