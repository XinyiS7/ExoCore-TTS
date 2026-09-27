# 0003 — Checkpoint B 验收包（真实 Vox 路径接通）

> **提交**：`a82f83a`（`a82f83a559eee0ee497b10fecd38d69a020851a1`）
> **范围**：`Plan/0003_tts_daemon.md` §8 第 4 步 —— 抽共用 Vox 低层入口、接 `backends/voxcpm2.py`、证明 casting 不回归、真机基本 smoke（§9.2 步骤 1–3）
> **明确不含**：delivery 产品门（§8 第 5 步）——按索哥指示，未进入、未生成对照音频，等待 Alicia 人耳；README / `voices.py` / `AGENTS.md` 文档收尾（第 6–7 步）也未动。
> **状态**：Builder 已停止施工，等待独立验收（gpt-5.6-sol / Solaire）。

---

## 0. 30 秒摘要

- 新增 `voxcpm.py`（casting 与 daemon 唯一的模型调用点）与 `backends/voxcpm2.py`（生产引擎，已进 registry）。
- `casting.py` 的 `_load_model`/`_synthesize` 内部实现下沉到 `voxcpm.py`；**manifest 字段、CLI 行为、65 个基线用例零改动全绿**。
- 全量测试 **151/151 OK**（65 基线 + 86 新增；本次 B 新增 19 例），连跑 3 次稳定，全部不加载模型/不联网。
- 真机 smoke（sandro_v1、14 字中文）：**cold → loading（56 s）→ ready → 200 + 445,484 字节 WAV**；加载 53.56 s、单段推理 20.22 s（RTF 4.357）；加载期间 `/health` 每 3 秒一探、18 次全部 `loading`，无一次不可达；退出后显存回到基线。
- `delivery` 保持显式 `422 delivery_unsupported`（探针属第 5 步，未做）。

---

## 1. 复现命令

```bash
cd D:/Alicia/ExoCore_Project/ExoCore-TTS
git show --stat a82f83a
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v     # 151/151 OK

# casting CLI 快速回归（不加载模型）
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py list --voices
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py design --designs <file> --lines <file> --out <dir> --dry-run

# 真机（会加载模型，约 55 s；跑完记得停掉进程以释放显存）
EXOCORE_TTS_PORT=8791 E:/Miniconda3/envs/voxcpm_runtime/python.exe -m exocore_tts.server
```

smoke 产物（本仓 `.gitignore` 已忽略 `candidates/`，仅供人耳/复核）：`candidates/m2_smoke_B/sandro_v1_cold_smoke.wav` + `smoke.txt`。

---

## 2. 交付文件与改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/exocore_tts/voxcpm.py` | 新 | `MODEL_ID` / `DEFAULT_CFG` / `DEFAULT_TIMESTEPS`、`LoadedModel`（model + sample_rate + torch + `release()`）、`load_model()`、`generate()`（返回采样 + manifest 同形的计时/RTF/峰值事实）。**唯一** import torch/voxcpm 的地方 |
| `src/exocore_tts/backends/voxcpm2.py` | 新 | 生产引擎：资产→调用翻译、内部 seed、preflight、delivery 显式拒绝、设备日志 |
| `src/exocore_tts/casting.py` | 改（−62/+24） | 删本地 `_load_model`/`_synthesize` 实现，改用 `voxcpm.*`；`MODEL_ID` / `DEFAULT_CFG` / `DEFAULT_TIMESTEPS` 改为同名重导入（对测试与 tools 的可见性不变） |
| `src/exocore_tts/backends/base.py` | 改 | 协议新增 `check_asset()`（见 §6 偏差 a） |
| `src/exocore_tts/backends/fake.py` | 改 | `check_asset()` 空实现（解释性 docstring） |
| `src/exocore_tts/backends/__init__.py` | 改 | `default_backends()` 返回 `{"voxcpm2": VoxCpm2Backend()}`（构造不 import 重栈） |
| `src/exocore_tts/service.py` | 改（+1 行） | admission 之前调用 `backend.check_asset(asset)` |
| `tests/test_voxcpm_backend.py` | 新（17 例） | 低层入口 + 翻译层 + import 卫生 |
| `tests/test_service.py` | 改（+2 例） | 真 backend 类的服务级 503/422 且"不加载模型" |
| `tests/test_runtime.py`、`tests/test_server.py` | 改（分别 +5 / +4 行） | 仅加 `NullHandler`：注入故障的用例不再往 unittest 输出打 traceback（断言不依赖日志文本） |

---

## 3. 需求 → 证据对照（§8 第 4 步的四项目标）

| 目标 | 证据 |
|---|---|
| ① 抽出共用 Vox 低层入口 | `grep -rn "import torch\|from voxcpm" src/ tools/` 只剩 `voxcpm.py:54-55` 两行（casting / voxcpm2 都只 import `exocore_tts.voxcpm`）。`tests/test_voxcpm_backend.py::ImportHygieneTests::test_the_daemon_never_imports_the_heavy_stack_on_start_up`：子进程里 `import exocore_tts.server, exocore_tts.backends; default_backends()` 后断言 `sys.modules` 无 `torch`/`voxcpm` |
| ② casting 不回归 | `tests/test_casting.py`、`test_verify.py`、`test_cloud.py`、`test_voices.py` 四个文件**零改动**（`git show --stat a82f83a` 可核），65 例全绿；casting 的实际 diff 是"删内部实现 → 调 `voxcpm.load_model`/`voxcpm.generate`"，manifest 字段名与统计键由 `GenerateTests::test_facts_keep_the_manifest_shape`（key 集合精确相等）与既有 `test_pick_freezes_the_chosen_candidate_into_a_voice` 共同钉住 |
| ③ 接 `backends/voxcpm2.py` | 翻译层：`test_synthesize_translates_the_asset_and_owns_the_seed`（text/cfg=3.5/steps=16/prompt_text/reference 路径逐一断言；seed 为内部生成且在 `[0, 2^31)`；带 `seed=` 调用直接 `TypeError`）。preflight：`test_missing_reference_clip_is_engine_unavailable`、`test_corrupt_reference_clip_is_engine_unavailable`、`test_unusable_generation_defaults_are_engine_unavailable`（4 种坏值）。能力门：`test_delivery_is_refused_until_the_listening_probe_passes`。服务级：`test_missing_reference_clip_fails_before_any_model_load` 与 `test_delivery_is_refused_before_any_model_load`（都用 mock 让 `voxcpm.load_model` 一旦被调用即报 `AssertionError`，从而证明两条失败路径**都没有**先加载模型） |
| ④ 真机基本 smoke | §5 全记录 |

---

## 4. 新增 19 例逐条断言（B 部分）

**`tests/test_voxcpm_backend.py`（17）**

| 用例 | 决定性断言 |
|---|---|
| `LoadModelTests::test_reads_the_sample_rate_from_the_model` | `loaded.sample_rate == 24000`；`from_pretrained(model_id=..., load_denoiser=False)` 参数被记录 |
| `::test_reads_the_sample_rate_from_the_inner_tts_model` | 只有 `model.tts_model.sample_rate` 时取到 16000 |
| `::test_falls_back_when_the_model_exposes_no_sample_rate` | 落到 `voxcpm.FALLBACK_SAMPLE_RATE` |
| `::test_refuses_to_load_without_cuda` | `device=None` 且 CUDA 不可用 → `RuntimeError` |
| `::test_release_drops_the_model_and_empties_the_device_cache` | `model is None` 且 `cuda.empty_cache()` 恰 1 次 |
| `::test_release_on_a_cpu_only_machine_does_not_touch_the_cache` | CPU 机不调用 `empty_cache` |
| `GenerateTests::test_clone_kwargs_pair_the_reference_with_its_transcript` | `reference_wav_path == prompt_wav_path == ref`，`prompt_text` 同传（保留"同段提示"行为） |
| `::test_absent_reference_or_transcript_keeps_the_kwargs_minimal` | 无参考时 kwargs 仅 3 键；有参考无逐字稿时无 `prompt_text` |
| `::test_seed_is_applied_and_the_peak_counter_is_reset` | `torch.manual_seed`/`cuda.manual_seed_all` 收到同一 seed；峰值统计复位 1 次；进入 `inference_mode` 1 次 |
| `::test_facts_keep_the_manifest_shape` | facts 键集合 == `{infer_s, audio_s, rtf, peak_allocated_mb, sample_rate}` 且 `rtf == round(infer_s/audio_s, 3)` |
| `VoxCpm2BackendTests::test_delivery_is_refused_until_the_listening_probe_passes` | `supports_delivery() is False` 且 `synthesize(..., delivery)` → `DeliveryUnsupported` |
| `::test_a_valid_asset_passes_the_preflight` | 合法资产 `check_asset` 不抛 |
| `::test_missing_reference_clip_is_engine_unavailable` | 无 `reference.wav` → `EngineUnavailable` |
| `::test_corrupt_reference_clip_is_engine_unavailable` | 参考文件非音频 → `EngineUnavailable` |
| `::test_unusable_generation_defaults_are_engine_unavailable` | `"hot"` / `0` / `-1.0` / `16.5` 四种坏值 → `EngineUnavailable` |
| `::test_synthesize_translates_the_asset_and_owns_the_seed` | 见 §3③；另断言返回 `AudioResult.sample_rate == 24000`、样本长度 == 生成长度 |
| `ImportHygieneTests::test_the_daemon_never_imports_the_heavy_stack_on_start_up` | 子进程启动路径 `sys.modules` 无重栈 |

**`tests/test_service.py`（+2）**：`VoxCpm2ServiceTests::test_missing_reference_clip_fails_before_any_model_load`（→ `EngineUnavailable`）、`::test_delivery_is_refused_before_any_model_load`（→ `DeliveryUnsupported`），两者都在 `load_model` 被 mock 成 `AssertionError` 的前提下通过。

---

## 5. 真机 smoke 记录（§9.2 步骤 1–3）

环境：RTX 3060 Ti（跑前 1071 MiB/8192 MiB 占用，跑后相同）；系统空闲 commit 40.6 GB；`env = voxcpm_runtime`；`EXOCORE_TTS_PORT=8791`，无 token。

```text
[1] /health（任何请求之前）        -> 200 {"status":"ok","state":"cold"}
[2] POST /tts {"text":"今天风挺大的，把窗户关上吧。","voice_key":"sandro_v1"}
                                   -> 200, audio/wav, 445484 bytes, 总耗时 73.88 s
[3] 加载期间 /health 时间线（每 3 s 一探，全程 200，无一次不可达）
    02:47:12 .. 02:48:05  {"status":"ok","state":"loading"}   （18 次）
    02:48:08 .. 02:48:27  {"status":"ok","state":"ready"}     （8 次）
[4] daemon 日志关键行
    serving on http://127.0.0.1:8791 (auth: none; engines: voxcpm2; single process only)
    voxcpm2 ready: sample_rate=48000 device=NVIDIA GeForce RTX 3060 Ti
    engine voxcpm2 ready in 53.56s
    segment 1/1 chars=14 frames=222720 infer=20.22s rtf=4.357
    render ok voice=sandro_v1 engine=voxcpm2 segments=1 wav_bytes=445484 wall=73.77s
[5] 响应 WAV 复验（soundfile）
    222720 frames @ 48000 Hz mono PCM_16 = 4.64 s
    finite=True, peak=0.9953, rms=0.1228, 近静音帧 59393/222720（≈27%，含停顿，符合语音）
[6] 进程退出后显存回到 1071 MiB（无残留）
```

结论（可核事实）：冷请求会等待加载并直接返回音频；`/health` 在加载期间既**可响应**又如实报 `loading`；`cold → loading → ready → WAV` 成立；产物是可解码的 48 kHz 单声道 PCM_16。

---

## 6. 施工偏差与决策

| # | 事实 | 理由 | 契约影响 |
|---|---|---|---|
| a | Backend 协议新增 `check_asset(asset)`（fake 空实现；voxcpm2 校验参考文件与 generation defaults）；service 在 admission 前调用 | 否则坏资产要先付 ~54 s 模型加载才失败；§2.1 的"reference 资产损坏/缺失 → 503"需要一条**不浪费 GPU** 的路径 | 无 wire 契约变化；属内部 seam 扩展，已在 base.py 写清语义 |
| b | 新增 `tests/test_voxcpm_backend.py`（计划 §7 测试清单未列） | 按"测试文件可按职责合并"原则，把"低层入口 + 翻译层 + import 卫生"独立成文件 | 无 |
| c | `DEFAULT_CFG` / `DEFAULT_TIMESTEPS` / `MODEL_ID` 迁到 `voxcpm.py`，casting 同名重导入 | daemon 需要同一份引擎默认值；`casting.MODEL_ID` 等名字仍可用（基线测试与 tools 无感） | 无 |
| d | `test_runtime.py` / `test_server.py` 各加一行 `NullHandler` | 注入式故障用例会打 traceback，影响验收阅读；断言不依赖日志文本 | 无（测试基础设施） |
| e | generation defaults 的数值可用性校验放在 `check_asset`（坏值 503，不 500） | "资产损坏"属于 503 语义；避免坏 manifest 变成神秘 500 | 无 |
| f | 请求内判定顺序固定为 backend 存在 → `check_asset` → delivery 能力 | 坏资产（503）优先于能力不足（422）；与 §2.1 码表不冲突，且顺序在 `service.synthesize` 与用例里都是显式的 | 无 |

**未记录的隐式偏差**：无。65 个基线用例文件、三条冻结 WAV 与 `voice.json` 均未触碰。

---

## 7. 已知边界与风险（供验收参考）

1. **冷加载时长 53.56 s**（计划记录约 42 s）：本次含 HuggingFace 缓存校验与库内 warmup。§2.4 要求调用方 timeout 覆盖冷加载——M3 应按 ≥ 90 s 设计（这是一条新数据点）。
2. **模型库在 load 时访问 HF 缓存索引**（日志可见 revision 查询与 9 文件校验；很快，命中本地缓存）。daemon 自身不联网，但完全离线部署需要设置 `HF_HUB_OFFLINE=1`——M2 未新增配置项（§6 表已冻结）。
3. `voxcpm` 的 tqdm 进度条会出现在 daemon stdout（诊断噪音，不进契约）。
4. RTF 4.357（timesteps 16、14 字）→ 长文本单请求会持续占用单卡，与 §2.1"可能需要数分钟"一致。
5. smoke 只覆盖 zh/`sandro_v1` 的基本链路；en/de、多段拼接真机、idle 卸载真机、loader 失败真机 = §9.2 步骤 4–8，属 M2 收口前待办（本轮按索哥指示停在基本 smoke）。
6. `delivery` 仍固定拒绝；探针对照音频与映射（`422` 还是启用）需 Alicia 在场，未启动。

---

## 8. 建议对抗点

1. `grep -rn "import torch\|from voxcpm" src/` 应只剩 `voxcpm.py`；或直接跑 `ImportHygieneTests`。
2. 用 `sandro_en_v1` / `sandro_de_v1` 各发一句（真机路径同构，但值得确认资产/参数读取无误；需注意显存与时间）。
3. 把 `delivery` 塞进真机请求 → 期望 422 且日志中**没有**任何加载/推理行。
4. 临时改名 `voices/sandro_v1/reference.wav` 发请求 → 期望 503 且无加载；测后改回（本仓资产请勿提交改动）。
5. `cast.py design/clone --dry-run` 与 `cast.py list --voices` 行为应与 B 之前完全一致（本轮已跑，输出正常）。
