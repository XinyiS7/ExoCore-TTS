# Plan 0004 — CP-G1 evidence (offline production integration)

> **Builder evidence, awaiting independent acceptance; not a verdict.**
> 执行：Ecki（pane 3）　基线：`ExoCore-TTS` main `ec801de`　日期：2026-09-27
> 独立验收 owner：Solaire（`Plan/0004_gemini_production_renderer_acceptance.md` 为验收方写入）

## 1. Scope actually built (CP-G1 only)

把既有 Gemini client 提升为 daemon 的正式 backend，并让「一次请求 = 一次付费 provider render」
成为结构性事实。**未做**：任何真实 provider 调用、key 读取、CP-G2、`send_voice_msg`。

| 文件 | 变更 |
|---|---|
| `src/exocore_tts/backends/gemini.py` | 新增：`engine = "gemini"` backend（check_asset / load / unload / synthesize；`supports_delivery()=True`；SDK 与 key 只在 `load` 里触碰） |
| `src/exocore_tts/backends/base.py` | 协议新增 `plan_segments(text)`：分段策略归 backend 所有 |
| `src/exocore_tts/backends/{fake,voxcpm2}.py` | 实现 `plan_segments` → 本地确定性切分（行为不变） |
| `src/exocore_tts/service.py` | 用 `backend.plan_segments(clean_text)`；删除全局 `segment_text` 调用；**无** engine 特判 |
| `src/exocore_tts/voices.py` | `VoiceAsset.cloud_voice` 受管字段 + `cloud_voice_ref()`（fail-closed 解析）+ `save_cloud_voice()`（原子、拒绝静默覆盖） |
| `src/exocore_tts/backends/__init__.py` | registry 注册 `gemini`（构造保持 import-light） |
| `src/exocore_tts/cloud.py` | `load_sdk()`：SDK 缺失 = `CloudError`（→ 503），不是逃逸的 ImportError |
| `tools/register_cloud_voice.py` | 新增：受管登记工具（`--preflight` 花 1 次 render 先验证后登记；无 `create_voice` 路径） |
| `pyproject.toml` | 可选依赖 `cloud = ["google-genai>=2,<3"]`（不进 ExoCore requirements） |
| `tests/test_gemini_backend.py` | 新增 21 项离线测试（见 §3） |
| `tests/test_voices.py` | 追加 6 项云资产 schema 测试（round-trip / 形状拒绝 / 不静默覆盖 / 旧 manifest 无损） |
| `tests/test_service.py` | `StubBackend` 同步新增 `plan_segments`（断言未改） |
| `README.md` | 云端引擎小节 + CP-G1 里程碑行（明确未 PASS） |

## 2. 施工中做的设计决定（供验收核对）

1. **engine key = `"gemini"`**（计划未指定 registry 字符串）。
2. **引用形状 `cloud_voice = {"kind": "id"|"name", "value": "..."}`**：两种 kind 一对一映射到既有
   `CloudVoiceClient.render` 的真实 kwargs（`voice=` / `prebuilt=`，SDK 字段
   `VoiceConfig.voice` / `PrebuiltVoiceConfig.voice_name`）——不猜槽位、不 fallback、不重试。
3. **`plan_segments` 作为协议方法**：满足 §2.4「不做 `engine == ...` 散落特判」；本地两引擎保持
   既有切分（回归全绿），云端整段一次调用。
4. **CP-G1 不登记真实 `voices/sandro_gemini_v1/voice.json`**：登记发生在 CP-G2 第 0 步（工具
   + `--preflight`），理由是不把未经真机验证的身份写进正式资产库；当前状态下该资产缺失 →
   `404 unknown_voice`，配置不完整 → `503`，两种都 fail closed。（若验收方要求提前登记，
   可用工具一条命令补，不需要改代码。）
5. **SDK 检查前移到 `load`**（`cloud.load_sdk()`）：满足 §2.5「SDK dependency 在 render admission
   前不可用 → 503」；缺失时不会走到 provider。
6. CP-G2 素材线索（**未登记、未验证**）：`ai_studio_code.py`（工作区里 Alicia 的 AI Studio
   模板，untracked、无明文凭据）用 `prebuilt_voice_config(voice_name="Ale 2.5 2")` 指向原始声线，
   且 style 以 `"Style: "` 前缀传入。CP-G2 第 1 步 preflight 就以「当前 key + 该引用」为准。

## 3. 验证事实（全部离线，无网络、无 key、无 GPU 加载）

- **全量非网络测试：187/187 OK**（基线 160 → +27；命令
  `E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests`，2.7s；独立复跑一致，
  见 `Plan/0004_gemini_production_renderer_acceptance.md` §2。曾一度误记为 185，已按验收提示更正）。
- 测试覆盖（对 Plan §5 的离线部分）：
  - 真 `/tts` entry path + 注入的离线 client → 200 `audio/wav`，`delivery` 原样进 style、不进行文；
    `id`/`name` 两 kind 只落对应槽位（G-03/G-05 离线部分）；
  - 接近 admission 上限的长文本 → provider 调用**恰为 1 次**且文本完整（G-04）；
  - client 抛错 / 返回不可读字节 → `500 synthesis_failed` 且**没有第二次调用**（G-04/G-05 fail path）；
  - 资产缺引用 / 形状非法（4 种） → `503`，provider **0** 次调用（G-02 fail closed）；
  - 默认注册表（不注入）在 key 不可达时 `503` 且引擎回到 `cold`（可重试语义）；
  - wire 只认 `text/voice_key/delivery`，`style`/`model`/`voice_id` → `422 invalid_request`（G-01）；
  - 协议回归：本地两引擎 `plan_segments` 仍切分且拼接无损（G-07 离线部分）；
  - 形状回归：backend 的调用 kwargs 用真 `CloudVoiceClient.render` 签名 `bind()` 验证；
    registry 构造在子进程里证明不 import `torch`/`voxcpm`/`google`。
- **真 daemon 离线 smoke**（隔离 `EXOCORE_TTS_VOICE_ROOT` + `EXOCORE_TTS_DOTENV=<不存在>` +
  `GEMINI_API_KEY=`，端口 8770，全程无 provider 调用）：
  - 启动行：`engines: gemini, voxcpm2`；`/health` = `{"status":"ok","state":"cold"}`；
  - 云资产 + 无 key → `503 {"error":"engine_unavailable"}`，随后 `/health` 仍 `cold`；
  - 未知资产 → `404 {"error":"unknown_voice"}`；额外字段 → `422 {"error":"invalid_request"}`；
  - daemon 停止后显存回到基线（1303 MiB used，起止一致）。
- 未触碰：`.gitignore`（工作区里的一处非本计划修改，保持未暂存）、`ai_studio_code.py`、
  `ExoCore/`、`ExoCore-Runtime/`、Desktop、现有 `voices/**` 参考音频与三套本地 manifest。

## 4. HOLD / 待裁决

1. **CP-G2 未跑**（Alicia 配对 key + 原始 voice 后按 Plan §3 固定顺序执行）。
2. **CP-G2 计数口径需要 plan owner 裁决**：固定顺序 = 1(preflight) + 5(zh/en/de/it/mixed) +
   2(no-delivery/delivery A-B) = **8** 次 provider render，与「首轮上限 7 次」冲突。可能的解法
   （需 Solaire 选定，不由 builder 改冻结门）：preflight 与 zh 合并、A-B 复用其中一条样本、或
   Alicia 明确同意 8 次。
3. 独立验收（Solaire）通过前不推送；`README` 里程碑保持「未 PASS」表述。
