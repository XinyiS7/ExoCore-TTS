# 0003 — TTS 守护进程（M2）

> **性质**：施工计划。落地 `POST /tts` + `GET /health` + `GET /voices`，一条本地真推理路径 + 一个不碰 GPU 的契约替身。
> **日期**：2026-09-26　**署名**：deepseek-flash / Ecki
> **前置**：M1（骨架 + 选角台）、M1.5（`register` + 三条声线）、M1.7（云端参考链路入库）

## 目标

把选角成果变成一个 ExoCore 可调用的端口：**声音资产留在工厂这一侧**，ExoCore 只说"朗读这段文字、用这个 `voice_key`"，具体是本地还是云端由资产自己声明。端口不可用时必须如实回答，不许把"正在加载"说成"服务离线"。

## 行为 / 范围

### 1. `POST /tts`

请求：

```jsonc
{
  "text": "<要朗读的文本>",
  "voice_key": "sandro_v1",
  "style": "<可选：覆盖资产里的 baseline_instruction>",
  "seed": 12345,                    // 可选；缺省随机（同一条消息由 ExoCore 侧缓存，不重复合成）
  "defaults": { "cfg_value": 3.5, "inference_timesteps": 16 },   // 可选：覆盖资产默认
  "verify": false                   // 可选：开启本地质检 + 重试
}
```

响应：**裸 `audio/wav` 字节**（形状已在 README 冻结），附实测头 `X-TTS-Engine` / `X-TTS-Audio-Seconds` / `X-TTS-Infer-Seconds` / `X-TTS-Rtf` / `X-TTS-Segments`。

错误（全部 JSON，`{"error": ...}`）：

| 情况 | 状态 | `error` |
|---|---|---|
| 未知 `voice_key` | 404 | `unknown_voice` |
| 空文本 / 超长 / 参数非法 | 422 | `invalid_request` |
| 资产声明的 engine 没实现（如 `engine: gemini`） | 503 | `engine_unavailable` |
| 合成失败 | 500 | `synthesis_failed`（原因已经过 `cloud.scrub_secrets`） |

**加载中不报错、只等待**：请求会阻塞到模型就绪；期间 `GET /health` 报 `loading`，前端（M3）据此显示「声音加载中」。这样"正在启动"和"服务没开"是两件不同的事。

### 2. 长文本分句

朗读整条消息时文本可能很长（本地模型长段落峰值 ~6.4GB）。守护进程按**句末标点**切段（每段 ≤ `EXOCORE_TTS_SEGMENT_MAX_CHARS`，默认 120），逐段合成后用短静音（默认 250ms）拼接，段数回报在 `X-TTS-Segments`。

- 切段是纯逻辑 → 可单测（中英德标点，含引号内标点、省略号、无标点长串的兜底硬切）；
- 分句**有意放在工厂侧**：ExoCore 侧保持"投影文本 → 调端口"的薄适配，不碰音频拼装；
- 文本投影（去掉 markdown/代码块等）是 ExoCore 侧的责任，守护进程不做内容加工。

### 3. `GET /health`

```jsonc
{
  "status": "ok",
  "backends": {
    "voxcpm2": { "state": "cold|loading|ready", "loaded": false, "elapsed_s": 0.0,
                 "expected_s": 45, "device": "<cuda name>" },
    "fake":    { "state": "ready", "loaded": true }
  },
  "voices": [{ "key": "sandro_v1", "engine": "voxcpm2", "display_name": "Sandro (zh)" }]
}
```

`expected_s` 用实测回填（冷启动 ≈ 42s，首次推理含 torch.compile 另有 25–35s）。

### 4. `GET /voices`

可用 `voice_key` 清单（key / engine / display_name），供 ExoCore 侧校验与展示。

### 5. 引擎生命周期（`engine.py`）

- 启动**不加载**模型（已裁决口径 1）；
- 第一条合成请求触发加载；加载期间 `state=loading`，并发请求**排队**（单 GPU 串行，一把锁；合成与卸载共用该锁）；
- 空闲 `EXOCORE_TTS_IDLE_UNLOAD_SECONDS`（默认 1800，0 = 不卸载）后卸载：丢引用 + `torch.cuda.empty_cache()`，状态回 `cold`；
- 状态查询不加锁（只读字段）。

### 6. backend 分派

资产的 `engine` 字段决定路径（三条现在都是 `voxcpm2`）：

- `backends/voxcpm2.py`：复用 `casting` 的加载与合成。把 `_synthesize` 里"生成采样"的部分提成不落盘的公开入口（`generate_samples`），`_synthesize` 仍写文件，行为不变；
- `backends/fake.py`：确定性的正弦/静音 WAV，**不碰 GPU、不联网**，供契约测试；
- 未实现的 engine → `503 engine_unavailable`，不猜、不降级。

### 7. 质检钩子（默认关闭）

`"verify": true` 时合成后跑**本地**质检：静段残留电平（实测能抓出单条抽卡的"回声/房间感"）+ 时长合理性；不合格自动重试（上限 3 次，每次换 seed）。阈值与实现在 `quality.py`，纯函数、可单测。
**ASR 文本抽检不进本次**（要云端调用、有成本与延迟，等 M3 的前端状态设计定了再议；工具 `tools/verify_audio.py` 仍可手工跑）。

### 8. 配置（`config.py` 增补）

`tts_host`（默认 `127.0.0.1`）、`tts_port`（默认 `8769`）、`tts_idle_unload_seconds`、`tts_expected_load_seconds`、`tts_segment_max_chars`、`tts_gap_ms`、可选 `EXOCORE_TTS_TOKEN`（设了才校验 `Authorization: Bearer`；默认不设，服务只绑 loopback）。
`pyproject.toml`：`server` extras 已就绪，补 `dev = ["httpx>=0.27"]`（TestClient 需要）与入口 `exocore-tts = "exocore_tts.server:main"`。

## 不做（明确划出）

- ExoCore 适配器改 key-based、`base_url` 配置化、状态区分 —— **M3**，跨仓计划落 `ExoCore/Plan/`；
- `send_voice_msg` 工具与独立语音条 UI —— **M4**；
- 云端 backend 的正式实现 —— 本次只定 `engine` 分派语义（云端渲染工具已入库，接入时加一个模块即可）；
- ASR 文本抽检、admin/强制卸载接口、多进程/多 GPU 调度（空闲阈值够用）。

## 关键文件

```text
src/exocore_tts/engine.py          引擎状态机 + 锁 + 空闲卸载 + 分句拼接
src/exocore_tts/server.py          FastAPI 三端点 + 错误映射 + main()
src/exocore_tts/quality.py         静段残留 / 时长合理性（纯函数）
src/exocore_tts/backends/base.py   backend 协议（state / render / available）
src/exocore_tts/backends/fake.py   契约替身
src/exocore_tts/backends/voxcpm2.py 真推理
src/exocore_tts/casting.py         （改）拆出 generate_samples
tests/test_engine.py / test_server.py / test_quality.py / test_segments.py
```

## 施工顺序

1. `engine.py` 状态机 + 分句 + 拼接（注入 stub loader / 假时钟，纯逻辑可测）；
2. `backends/fake.py` + `server.py` 三端点 + 契约测试（**全程不需要 GPU**）；
3. `casting` 拆出 `generate_samples` → `backends/voxcpm2.py`；
4. 质检钩子 + 重试；
5. `config.py` / `pyproject.toml` / README / AGENTS 同步；
6. 真机冒烟。

## 验证

- **单测**（`python.exe -m unittest discover -s tests`，服务环境）：契约 shape、未知 key、空/超长文本、engine 分派与 503、状态机（cold→loading→ready→idle→cold）、串行锁、分句（中英德标点 + 硬切兜底）、质检指标与重试。**不加载模型、不联网。**
- **真机冒烟**（记录进本文件附注）：
  1. 起服务 → `GET /health` 应为 `cold`（显存无占用）；
  2. `POST /tts`（`sandro_v1`，一句中文）→ 期间 `/health` 应先 `loading` 后 `ready`，记录冷启动秒数与 RTF；
  3. 产出用 `soundfile` 复核时长/采样率，并用 `tools/verify_audio.py` 抽检文本；
  4. 长文本一条（>300 字）验证分句拼接（`X-TTS-Segments` ≥ 2）；
  5. 空闲阈值设 60s，等 90s → `/health` 回 `cold`，`nvidia-smi` 显存释放；
  6. 英/德各一条（`sandro_en_v1` / `sandro_de_v1`）确认多语言口径未回归。
- **跨仓契约**：`X-TTS-*` 头与 `/health` 字段即为 M3 的实现依据，形状改动必须同步 README。

## 风险 / 前置条件

- **页面文件**：加载 4.58GB 权重需要空闲提交内存 ≥ ~8GB。本机 C 盘页面文件只有 3GB，实测 4 次加载中 2 次死于 `OSError 1455` / segfault。**开工前建议提到 16GB**，否则守护进程每次冷启动都在赌运气。
- **冷启动成本**：加载 ≈ 42s + 首次推理 25–35s（torch.compile）。`/health` 的 `expected_s` 与 M3 超时口径都按这个来（ExoCore 现有 10s 超时 + 60s 假死阈值不够用）。
- **单 GPU 串行**：并发请求排队；M3 侧不得用短超时掩盖，要显示"加载/排队中"。
- 依赖已核对：服务环境 `fastapi 0.141.1` / `uvicorn 0.52.4` / `httpx 0.28.1` 齐备。

## 附：本次已确认的口径

1. 空闲卸载默认 30 分钟（0 = 不卸载）；
2. 质检默认关闭，`"verify": true` 才开；
3. 云端 backend 本次不实现，只定 `engine` 分派语义；
4. 分句放在工厂侧（ExoCore 侧保持薄适配）。
