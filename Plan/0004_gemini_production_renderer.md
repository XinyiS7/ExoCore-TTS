# 0004 — Gemini production renderer（Gate-G）

> **状态**：READY FOR CONSTRUCTION；只授权本计划的 ExoCore-TTS scope。真实 smoke 在 Alicia 完成 key / 原始 voice resource 配对前保持 HOLD。
> **范围**：仅 `ExoCore-TTS`；不修改 ExoCore、ExoCore-Runtime 或 Desktop。
> **产品授权**：Alicia — 2026-09-27；现阶段只完成 Gemini TTS production renderer，后续 `send_voice_msg` checkpoints 不随本计划开工。
> **计划 / 独立验收 owner**：Solaire。
> **上位契约**：`ExoCore/Plan/send_voice_msg_Backend_Plan.md` §3.4–§4 Gate-G；本文件把该 Gate 落成 TTS 仓内施工与二元验收边界。
> **基线**：`ExoCore-TTS` main `ec801de`；既有 M2 daemon 与 VoxCPM2/read-aloud 行为均为 preserve boundary。

## 0. 先决事实与资产门禁

### 0.1 当前代码事实

现有 `src/exocore_tts/cloud.py::CloudVoiceClient` 能调用 Gemini、传 provider style 并返回 WAV bytes，但它只被选角/资产制作工具使用。生产 `/tts` 的 backend registry 当前只有 `voxcpm2`；因此 Gemini 还不是 daemon renderer。

当前 `tools/cloud/voices/ale.json` 记录的是：默认后端 Gemini key 所属 project 下，根据完整 prompt **重新创建的 replacement voice**。它不是 Alicia 所说的、位于专用 AI Studio project 下的原始 Ale voice resource。此前 reference WAV 与三个本地 Vox 资产的 provenance 指向该 replacement，只证明它参与过本地声音制作，不证明它是本计划要上线的原始云端 voice identity。

### 0.2 Project-bound identity（用户实测事实）

Gemini prompted voice resource 与创建它的 project/key 权限边界绑定。生产 key 与 voice resource 必须属于同一个可访问 project；只换 key 或只换 voice id 都不足以证明配对正确。

本计划冻结以下安全规则：

1. Alicia 在仓库外更新 `GEMINI_API_KEY`；key 不进入 Plan、git、命令输出、日志、错误响应或 evidence。
2. Alicia/Ecki 提供并登记**原始 Ale voice resource id**；resource id 可作为受管资产事实保存，但不得把 key 一并保存。
3. live preflight 必须先用“当前 key + 原始 resource id”做一次受控 render，正面证明可访问。
4. 若返回 not found / permission / project mismatch：立即停止并报告 `engine_unavailable`；禁止自动 fallback 到当前 replacement、prebuilt voice 或任意新 voice。
5. 本计划禁止调用 `create_voice()`、禁止 `--force` 重建、禁止再次靠 prompt 调出替代品。是否保留 replacement 作为历史制作证据，不影响 production routing。

## 1. 目标与完成定义

把既有 Gemini client 提升为 ExoCore-TTS 的正式、可替换 backend：

```text
POST /tts {text, voice_key, delivery?}
    -> managed Gemini asset
    -> fixed original Ale voice resource
    -> delivery mapped to Gemini style
    -> 200 audio/wav
```

Gate-G 只有在以下事实同时成立时才能 PASS：

- 现有 `/tts` wire shape 和 bounded error body 不变；
- 固定原始 Ale voice 可由生产 key 实际解析并合成；
- 无 style、有 style、中/英/德/意及混写均有真实证据；
- style 可听生效、不被朗读、不替换声音身份；
- 每个 HTTP 请求至多一次 Gemini render，不自动重试付费调用；
- VoxCPM2、本地三套资产及普通 read-aloud 回归不受影响。

## 2. 冻结边界

### 2.1 HTTP 与命名

生产接口继续只接受：

```json
{
  "text": "required final spoken text",
  "voice_key": "required opaque asset key",
  "delivery": "optional engine-neutral performance direction"
}
```

- wire 上不增加 `style`、provider voice id、model、language、seed 或 project。
- `style` 只存在于未来 Agent tool 产品面；进入 TTS port 前已经映射为 `delivery`。
- Gemini backend 内部才把非空 `delivery` 一对一传给 provider style。
- 空白 delivery 等价于 absent；不得拼进 text，也不得静默丢弃非空值。
- `text` admission、600 字符上限、`delivery` 500 字符上限、认证、健康端点及成功 `audio/wav` contract 全部沿用 M2。

### 2.2 受管 cloud asset

新增独立 opaque asset key：`sandro_gemini_v1`。

该资产：

- `engine` 指向 Gemini production backend；
- 通过明确的受管字段解析原始 Ale resource id，不把 provider id 塞进 display name 或调用方请求；
- 不要求 `reference.wav`，也不复用本地 `sandro_v1` / `sandro_en_v1` / `sandro_de_v1`；
- 必须由受管登记路径原子写入，不能靠手改一个会被 loader 静默忽略的未知 JSON 字段；
- resource 缺失、过期、无权限或配置不完整时显式失败，不 fallback。

若扩展 `VoiceAsset` schema，字段必须向现有 Vox manifests 保持无损默认值，并增加 round-trip / unknown-or-invalid shape regression；不要把 secret 或 project credentials放入 manifest。

### 2.3 Backend seam

Gemini backend 必须实现既有 backend lifecycle：asset check、lazy load、synthesize、unload，并在默认 registry 中注册。Google SDK 继续 lazy import；依赖进入 TTS 自己的可选 cloud/gemini dependency，不进入 ExoCore requirements。

- `load` 只建立轻量 provider client/config，不加载本地 GPU，也不改变 Vox idle-unload 语义。
- `check_asset` 在 provider 调用前拒绝缺失/非法 managed voice配置。
- `supports_delivery=True`；非空 delivery 原样传 provider style。
- provider WAV/container bytes 必须被严格解码、校验并转换为 daemon 统一的 `AudioResult`；最终仍由既有 service 产出可读取 WAV。
- 不把 provider model、resource id、路径或异常文本加入 HTTP body。

### 2.4 一次请求一次 provider render

现有 service 会为本地 Vox 将长文本分段并逐段调用 backend；该行为不能直接套到付费 Gemini backend，否则一个 HTTP 请求会变成多次 provider 调用。

施工必须增加最小、backend-owned 的 rendering/segmentation policy：

- Vox 与 fake 保持既有 deterministic segmentation；
- Gemini 在通过整次 text admission 后，以完整 text 调用 provider **恰好一次**；
- service 不允许通过 `engine == ...` 的散落特判实现；
- 任一失败不返回半成品、不自动重试。

### 2.5 错误与隐私

沿用既有错误词表：

- key/project/resource/SDK dependency/config 在 render admission 前不可用 → `503 engine_unavailable`；
- 已进入 provider synthesis 但无有效音频 → `500 synthesis_failed`；
- request shape/length → `422 invalid_request`；
- provider 原始错误、key、正文、delivery、resource id、本机路径均不得进入 HTTP body。

日志只保留必要的安全诊断分类与 timing；任何 provider异常都经过既有 secret scrub。测试替身必须证明一次调用失败后没有第二次 provider call。

## 3. 施工 checkpoints

### CP-G1 — Offline production integration

Scope：backend、asset schema/registration、registry、service rendering policy、依赖声明、离线测试与文档。

完成条件：

- fake Gemini client 可从真实 `/tts` entry path 走到单次 render；
- delivery、asset resolution、WAV normalization 与 bounded errors全部可观察；
- 非法/不可访问资产在 provider 前拒绝；
- Vox/fake/daemon 全量非网络 regression 通过；
- 无 live call、无 key 输出、无 Django 改动。

### CP-G2 — Original Ale live capability gate

前置：Alicia 已在仓库外换好对应 project 的 key，并给出原始 Ale resource id。

固定顺序：

1. 单次最小 preflight，证明 key 能访问指定原始 resource；失败即 HOLD，不创建 replacement。
2. 通过 production `/tts` entry path 生成：中文、英文、德文、意大利文、至少一条混写。
3. 同一短文本做 no-delivery / delivery A-B；由 Alicia 听判：目标表演差异、指令未被朗读、Ale identity 未漂移。
4. 记录请求类别、状态、WAV 可读性、语言与听判结论；不记录 key、完整私有正文或 provider 原始异常。

首次正式 smoke 上限为 **7 次 provider render**。超过上限、失败后批量重跑或改用新声音资源，必须先重新征得 Alicia 同意。

> **Plan owner 裁决（2026-09-27，CP-G1 验收附则）**：固定顺序合计 8 次与「首轮上限 7 次」冲突，裁决为 **A-B 复用中文样本**——步骤 2 的中文样本即步骤 3 的 A 腿（同一短文本、no-delivery），步骤 3 只新增 1 次 delivery 渲染；总计 1(preflight) + 5(语言) + 1(delivery) = **7 次**，在冻结上限内，MUST gates 不变。验收报告见 `Plan/0004_gemini_production_renderer_acceptance.md` §5。**（追加 2026-09-27 晚：实际执行授权为 ≤8，验证探针已用 6 次；解除 HOLD 后成功矩阵仍需 6 次 → 需 Alicia 追加授权；“不需要额外同意”一句据此作废。见验收报告 Addendum A1。）** **（追加 2，2026-09-27 深夜：probe #5 证明本声线带 style 才可合成；裁决——资产侧「基线风格」为 CP-G2 最小前提（wire 不动）；A-B 语义改为「基线默认 vs delivery 覆盖」；预算结构重排为 +6（B 腿先行、总 ≤14），待 Alicia 追加授权。详见验收报告 Addendum A2。）** **（追加 3，2026-09-28 凌晨：job 09 STOPPED——441 字符覆盖片段 404；结构性结论 = 全形态覆盖在冻结 wire（500 上限 < 1266）下不可行 ⇒ 交付形态改为 baseline-only；裁决：剩余 5 次按 A3 的 (a) 执行（job10 先行、失败即全停）；带基线资产的非空 delivery 改为确定性拒绝（delivery_unsupported 422，矩阵后落地）；听判集 = 5 样本（含 zh）。详见验收报告 Addendum A3。）** **（追加 4，2026-09-28 凌晨：job 10（中文 + 基线）仍 404——两对样本下唯一变量 = 正文脚本，假设 = 「全设计文本 且 正文 Latin」才可解析；裁决：余 4 次按 A4 分配（en、de、it、direct-call+zh，任一失败即停）；若 CJK 被 provider 级拒绝则 zh 与 mixed 不可得、G-06 无法按现文满足，属产品级发现；已批准向 Alicia 问一句零成本问题验证。详见验收报告 Addendum A4。）** **（追加 5，2026-09-28 凌晨 · 重要更正：A3 轮与 A4 轮的失败均为测试台 key 注入空串导致（静默回退另一 project 旧 key），非声线行为——再次更正后：短风格（≤500）可用、中文可用、daemon 与直连等价。裁决：A3-2 撤销、回退 A2 语义（基线 + delivery 逐字覆盖，实现即最终形态）；A4 结论作废；听判对 = zh 基线 vs zh 短风格；预算 14/14 用尽，矩阵需 +4（总 ≤18）待 Alicia 批准。详见验收报告 Addendum A5。）** **（追加 6，2026-09-28 凌晨：A5 轮四语（en/de/it/mixed）全绿、五语矩阵完成、预算 18/18；CP-G2 工程面 PASS（G-01..G-05、G-07 满足；G-04 全轮恰好一次）。G-06 待 Alicia 听判四条样本（身份+吐字）→ A7 → README/计划状态改 PASS → TTS 仓推送（16+ 条逐条披露）。详见验收报告 Addendum A6。）**

## 4. Expected file scope

预计只触及：

- `src/exocore_tts/backends/`：Gemini backend与 registry；
- `src/exocore_tts/cloud.py`：可复用 provider client/解码/安全错误边界；
- `src/exocore_tts/service.py` 与最小 backend protocol：一次请求一次 cloud render；
- `src/exocore_tts/voices.py`、managed registration tool与 `voices/sandro_gemini_v1/voice.json`；
- `pyproject.toml`：TTS 仓自己的 Gemini dependency；
- `tests/`：offline contract/regression；
- `README.md` 与本 checkpoint evidence。

不允许触及：

- `ExoCore/` 生产代码、models或 migrations；
- `ExoCore-Runtime/`；
- Desktop；
- 现有 Vox reference WAV 与三个本地 voice manifest；
- `.env`、任何真实 key 或未授权的新 cloud voice。

实际文件可因经验证的最小实现略作调整，但扩大到上述禁止范围前必须停下说明原因。

## 5. 独立验收 MUST gates

### G-01 Wire preservation

Given 既有 `/tts` contract，when 使用 Gemini asset，then 仍只接受 `text/voice_key/delivery`，额外 `style/model/voice_id` 字段为 `422 invalid_request`，成功为可读取 `audio/wav`。

### G-02 Identity ownership

Given `sandro_gemini_v1`，when render，then provider voice只能来自该受管资产；调用方不能覆盖。缺失、无权限、过期或 project mismatch 必须 fail closed，不使用 replacement/prebuilt/new voice。

### G-03 Delivery semantics

Given 同一 text 与固定 voice，when delivery absent/present，then provider分别收到 absent/exact style；style 不进入 spoken text。真实 A-B 还必须通过 Alicia 听判。

### G-04 Exactly one paid render

Given 一条接近 admission 上限且会触发本地分段的 text，when 使用 Gemini asset，then provider mock 调用次数恰为 1；failure path 同样不重试。

### G-05 Audio and error integrity

Valid provider output produces structurally valid WAV。空、损坏、非法采样或 provider failure 不返回半成品；错误体保持单一 bounded code且不泄漏 secret/content/resource/path。

### G-06 Multilingual live evidence

Production entry path 使用原始 Ale voice 实际生成 zh/en/de/it/mixed；transport成功不等于质量通过。Alicia 必须明确确认声音身份与 style A-B。

### G-07 Preserve M2

现有 Vox backend、三套本地声音资产、分段、single-worker lifecycle、health、authorization及完整非网络 test suite 全部通过；普通 read-aloud仍可继续使用本地 `voice_key`。

任一 MUST gate 未满足即 Gate-G **FAIL/HOLD**，不得把 `send_voice_msg` 对 G045 或 AGY 宣布为 production-ready。

## 6. 交付与停止条件

- CP-G1、CP-G2 分开提交和验收；真实 gate 未通过时可以保留 CP-G1，但不能声称 Gate-G PASS。
- 独立验收报告写 `Plan/0004_gemini_production_renderer_acceptance.md`；Builder 不修改冻结的 MUST gates 来换取通过。
- 通过后才更新 README milestone，并把本计划状态改为 PASS；不在本计划内继续施工 `send_voice_msg`。
- 任何“资源不可访问所以重调一个差不多的声音”都属于明确停止条件，必须交回 Alicia 决定。