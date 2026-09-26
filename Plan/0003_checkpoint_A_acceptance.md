# 0003 — Checkpoint A 验收包（非 GPU 地基）

> **提交**：`668b463`（`668b4635bbe54bb161538a6c08c0a85b57face09`）
> **范围**：`Plan/0003_tts_daemon.md` §8 第 1–3 步（§2 HTTP 契约、fake backend、文本分段/WAV 拼接、§5 runtime 状态机与并发、§6 配置/鉴权）
> **状态**：Builder 已停止施工，等待独立验收（gpt-5.6-sol / Solaire）。
> 本文件只陈述可复现事实、命令与证据映射；不含自我裁决。

---

## 0. 30 秒摘要

- 新增 TTS daemon 的**非 GPU 地基**：契约层（`server.py`）、请求编排（`service.py`）、单 worker 生命周期（`runtime.py`）、分段器（`text.py`）、backend 协议 + 契约替身（`backends/`）、领域错误（`errors.py`）、daemon 配置与启动门禁（`config.py`）。
- **未动** `casting.py` / `voices.py` / 已有 65 个测试与三条冻结声线；真实 Vox 路径、`voxcpm.py` 抽取、delivery 探针属 Checkpoint B。
- 自动化结果：**Ran 132 tests, OK**（65 基线 + 67 新增），连跑 3 次稳定；全程不加载模型、不联网。
- 生产 default registry 在 A 阶段**故意为空**：真实 daemon `/health` 可用，`/tts` 如实返回 `503 engine_unavailable`（唯一生产引擎 `voxcpm2` 在 §8 第 4 步接入）。这不是缺陷，是切片事实。
- 真机 smoke（loopback、无 token/有 token、非 loopback 拒绝启动）逐条留档在 §7。

---

## 1. 复现命令（唯一权威）

```bash
cd D:/Alicia/ExoCore_Project/ExoCore-TTS
git show --stat 668b463                     # 本切片
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v
# -> Ran 132 tests ... OK
```

手工冒烟（不加载模型，空 registry 的 A 阶段行为）：

```bash
EXOCORE_TTS_PORT=8791 E:/Miniconda3/envs/voxcpm_runtime/python.exe -m exocore_tts.server
curl -s http://127.0.0.1:8791/health
# {"status":"ok","state":"cold"}
curl -s -X POST http://127.0.0.1:8791/tts -H 'content-type: application/json' \
  -d '{"text":"你好。","voice_key":"sandro_v1"}'
# {"error":"engine_unavailable"}   # voxcpm2 未接（§8 第 4 步）
```

---

## 2. 交付文件

| 文件 | 说明 |
|---|---|
| `src/exocore_tts/server.py`（新） | FastAPI 端口：`POST /tts`、`GET /health`、bearer 中间件、错误体映射、CLI `main()` |
| `src/exocore_tts/service.py`（新） | 校验 → 分段 → backend 分派 → 静音拼接 → 结构校验 → raw WAV |
| `src/exocore_tts/runtime.py`（新） | 单 worker 队列、`cold/loading/ready`、exactly-once 加载、失败回滚、空闲卸载（锁内复核） |
| `src/exocore_tts/text.py`（新） | 标点感知分段，`SEGMENT_MAX_CHARS=120` |
| `src/exocore_tts/errors.py`（新，见 §5 偏差 a） | 稳定领域错误与 wire 错误码的唯一定义处 |
| `src/exocore_tts/backends/base.py`（新） | 最小 backend 协议 + `AudioResult` |
| `src/exocore_tts/backends/fake.py`（新） | 确定性、无 GPU/无网络契约替身（可注入慢加载/慢推理/失败） |
| `src/exocore_tts/backends/__init__.py`（新） | `default_backends()` 注册表（A 阶段为空，见 §5 偏差 b） |
| `src/exocore_tts/config.py`（改） | `DaemonConfig` + `load_daemon_config()` + 字面量 loopback 启动门禁 |
| `pyproject.toml`（改） | `exocore-tts` 入口、`dev` extra（httpx） |
| `tests/test_text.py` / `test_runtime.py` / `test_service.py` / `test_server.py`（新） | 67 个新用例 |

---

## 3. 实现事实（契约层，对齐 §2/§5/§6）

### 3.1 `POST /tts`

- 唯一字段：`text`（必填 str）、`voice_key`（必填 str）、`delivery`（str，缺省 `""`）；`extra="forbid"`，未声明字段一律 422。
- `text`：`strip()` 后非空、不得超 `EXOCORE_TTS_MAX_TEXT_CHARS`（默认 600，按 strip 后长度计）；`delivery`：`strip()` 后空白等价未提供、≤ 500（`MAX_DELIVERY_CHARS` 代码常量）；`delivery: null` 按类型错误 422（见 §5 偏差 c）。
- 成功：`200` + `Content-Type: audio/wav` + 完整可读裸 WAV（`GAP_MS=250` 静音段间拼接，单段无首尾 gap）。
- 失败：**所有**状态码下 body 恒为单字段 `{"error": "<code>"}`（测试用整字典相等断言，杜绝 `detail`/附加键）；状态码表按 §2.1 实现：401 `unauthorized` / 404 `unknown_voice` / 422 `invalid_request` / 422 `delivery_unsupported` / 503 `engine_unavailable` / 500 `synthesis_failed`。
- 响应头不含引擎/设备/RTF/分段数；异常文本只进日志。
- `/docs`、`/redoc`、`/openapi.json` 关闭 → 端口只有两个端点，不存在第二份机器契约。

### 3.2 `GET /health`

- 固定 `{"status": "ok", "state": "<cold|loading|ready>"}`，**恰好两个键**；冷加载期间可响应且不等待 worker；配置 token 时同一 bearer 校验。

### 3.3 鉴权

- `Authorization: Bearer <token>`，`secrets.compare_digest`；中间件在路由之前 → **auth 先于 body 校验**（空 text 无 token 也回 401 而不是 422）。

### 3.4 配置与启动门禁（§6）

| 环境变量 | 默认 | 约束 |
|---|---:|---|
| `EXOCORE_TTS_HOST` | `127.0.0.1` | 只接受**字面量 loopback**（`127.*` / `::1`）；`localhost`/`0.0.0.0`/`::`/DNS 名一律拒绝启动（exit 2） |
| `EXOCORE_TTS_PORT` | `8769` | 1..65535 |
| `EXOCORE_TTS_TOKEN` | 空 | 空白视为未配置 |
| `EXOCORE_TTS_IDLE_UNLOAD_SECONDS` | `1800` | ≥ 0；`0` 禁用（不启动监控线程） |
| `EXOCORE_TTS_MAX_TEXT_CHARS` | `600` | ≥ 1 |

- 单进程固定：`uvicorn.run(..., workers=1)`；`SEGMENT_MAX_CHARS=120`、`MAX_DELIVERY_CHARS=500`、`GAP_MS=250` 为代码常量，不进环境配置。

### 3.5 runtime（§5）

- 所有重活（load / synthesize / unload / WAV 编码）在**单 worker 线程**执行；HTTP 事件循环不阻塞。
- `cold→loading→ready`；加载失败 → 清引用回 `cold`，当前请求 503，下一请求可重试（已验证 load_count=2）。
- 并发首请求 exactly-once 加载（加载本身在 worker 队列里，天然只有单一 load owner）。
- 请求通过校验后先做 admission（同锁内登记 pending + 活动时间），完成后再更新时间；卸载决策在同一把锁内**逐条复核**（idle 阈值、无 pending、无渲染），重型释放仍在 worker 上执行。
- `ready` 与模型引用同锁同改，不存在 "ready 但引用为空"。

---

## 4. 需求 → 证据对照（§9.1 逐条）

| # | 计划要求 | 决定性测试与断言事实 |
|---|---|---|
| 1 | 只接受三个字段；旧 knobs/未知字段 → invalid_request | `test_server.py::TtsContractTests::test_unknown_fields_and_old_knobs_are_refused`：`style/seed/defaults/verify/format/engine` 六种载荷，逐一断言 422 且 body 整字典 == `{"error":"invalid_request"}` |
| 2 | 空白/超限/非法 key/未知声音/未实现 backend 的状态码与错误体 | `test_request_field_types_and_bounds`：缺 text、int text、空白、超限、`delivery:null`、501 字 delivery、坏 key → 422；未知 key → 404；并覆盖成功边界（text 恰 10、delivery 恰 500）。`test_engine_unavailable_when_no_backend_serves_the_engine` → 503 |
| 3 | 成功为可读 `audio/wav`；fake 与真实 backend 同请求 shape | `test_success_returns_readable_wav_bytes`：`content-type == "audio/wav"`（精确相等）、soundfile 解码非空、采样率 24000。同 shape 的可替换性由协议层证据补强：`test_service.py` 全部请求经 fake，结构门另用 `StubBackend` 同协议实现（真机端到端留 B） |
| 4 | 无 delivery 走基线；有 delivery 抵达 backend；不支持显式报错 | `test_delivery_reaches_every_segment_or_is_absent`：每段记录值 == 同一 delivery；空白 → `[None]`。`test_unsupported_delivery_is_refused_before_any_engine_work`：`DeliveryUnsupported` 且 `synth_count==0 && load_count==0`（未碰引擎）；HTTP 版 `test_delivery_unsupported_is_explicit_not_ignored` |
| 5 | bearer 开/关；non-loopback bind 启动拒绝（无论有无 token） | `test_without_a_token_the_port_is_open_on_loopback`；`test_with_a_token_both_endpoints_require_the_bearer`（无头/错 token/缺前缀 → 401 形状；health 同权；auth 先于 body）；`ConfigGateTests::test_non_loopback_hosts_are_refused_even_with_a_token`（6 个 host，含带 token）；`test_entry_point_refuses_a_non_loopback_host`（`main()` 返回 2） |
| 6 | 中英德标点/尾随引号/省略号/超长无标点切分；不丢字、不乱序、每段不越界 | `test_text.py` 13 例：尾随引号、`？！`/`……` 连排、`3.5` 与 `z.B./z. B.` 不断句、括号收尾、硬切 `["120","120","60"]`、空白回退不切词；`test_random_texts_never_lose_content_or_exceed_the_limit`（200 个定种子随机样本：非空、≤120、去空白拼接 == 原文） |
| 7 | 多段固定 gap 拼装；任一段失败不返回半截 | `test_segments_are_joined_with_the_fixed_silence_gap`：总帧数 == 段1+段2+gap 精确相等，且 gap 区间逐样本 == 0；`test_a_single_segment_gets_no_trailing_gap`；`test_any_failing_segment_fails_the_whole_request` |
| 8 | 非空/有限/合法 rate/channels/最终 WAV 可读；调用方不能指定 seed | `StructureGateTests` 六例（空、NaN、rate=0、3 通道、跨段 rate 不一致、纯 list 可接受）；`seed` 载荷 422（见 #1） |
| 9 | 两个并发冷请求只加载一次、推理不并发 | `test_runtime.py::test_concurrent_first_requests_load_once_and_never_overlap`：`load_count==1`、`max_concurrent_synth==1`；`test_many_concurrent_requests_never_overlap_and_load_once`（5 路，`synth_count==5`）；HTTP 版 `test_two_cold_requests_load_once_and_both_succeed` |
| 10 | 慢加载时 `/health` 不需等待 worker 且可观察 loading | `test_health_reports_loading_without_waiting_for_the_worker`：loader 被 gate 卡住时 `state()` 仍立刻返回 `loading`（<0.5s）；HTTP 版 `test_health_answers_loading_while_a_cold_request_waits`（POST 在途时 /health 200 + loading） |
| 11 | load 失败不永久 loading，下一请求可重试 | `test_load_failure_rolls_back_to_cold_and_the_next_request_retries`：503 后 `state==cold`，重试成功 `load_count==2`；HTTP 版 `test_load_failure_is_recoverable_over_http` |
| 12 | fresh request 与 idle unload 竞争，旧判断不能卸掉在用模型 | `test_eviction_recheck_cancels_for_an_in_flight_request`：时钟推进 60s 后（时间条件已满足）调用 `evict_if_idle()`，唯一拦截因素是 `pending==1`，`load_count` 仍 1；`test_eviction_cannot_act_on_a_stale_timer_snapshot`（假时钟复核活动时间）；`test_monitor_never_evicts_while_a_request_is_in_flight`（真实监控线程 0.01s 阈值下 ~20 tick 均不卸载） |
| 13 | health 只回 status/state；失败可恢复；不泄漏 queue/backend/device/异常 | `test_health_shape_is_exactly_status_and_state`（整字典相等）；`test_no_diagnostics_leak_through_headers_or_bodies`（响应头无 engine/device/rtf/segment/queue/voxcpm 字样）；所有错误用例共用 `assert_error()` 整字典相等断言 |
| 14 | casting 原有 design/clone/register/pick、manifest、原子 WAV 写入回归 | 原有 65 测试文件**未改动**，同一次 discover 里全绿（`tests/test_casting.py` 保留 manifest/atomic 写入用例） |
| 15 | 空闲卸载与关闭 | `test_idle_eviction_releases_the_model_and_the_next_request_reloads`（回 cold、`unload_count==1`、下请求 `load_count==2`）；`test_idle_unload_zero_disables_eviction`；`test_close_stops_the_monitor_and_releases_the_model`（幂等）；`test_app_shutdown_closes_the_engine`（lifespan 关闭路径） |

---

## 5. 施工偏差与优化（对照原 Plan）

| # | 事实 | 理由 | 契约影响 |
|---|---|---|---|
| a | 新增 `errors.py`（计划"预计新增"清单未列） | "稳定领域错误"是 §8 第 1 步要求的产物；独立模块让 `server.py` 只做映射、backend 也能抛同一词汇，避免 `backends → service` 反向依赖 | 无：错误码/状态码与 §2.1 完全一致 |
| b | `backends/__init__.py` 增加 `default_backends()`；A 阶段返回 `{}` | 唯一生产引擎 `voxcpm2` 属 §8 第 4 步；不放一个假引擎进生产注册表。空 registry 下 `/tts` 如实 503，`/health` 正常 | 无：503 的语义与 §2.1 一致；B 落地时该函数替换为真实 backend 一行 |
| c | `delivery: null` → 422（严格类型） | §2.1 写明"可选字符串、空白值等价未提供"，null 不属于允许类型 → 显式拒绝而非静默当缺省 | 属契约解释；若验收方认为应宽松，改动为 1 行 + 1 用例 |
| d | 契约表之外的 framework 路径（未知路由/错误方法/关闭的 /docs）也保证单字段形状，码值 best-effort（4xx→`invalid_request`，5xx→`synthesis_failed`） | §2.1 "错误体在所有状态码下都是这一个形状"；避免 FastAPI `detail` 泄漏 | 表内两端点的码值不变；表外路径本无冻结语义 |
| e | 关闭 `/docs` `/redoc` `/openapi.json` | 端口只应存在 §2 的两个端点，不产生第二份机器契约 | 无 |
| f | `main()` 中 `logging.basicConfig` 移到配置校验之后 | 启动被门禁拒绝时不留下全局副作用 | 无 |
| g | 按 §6 增 `exocore-tts` 入口与 `dev` extra；env 重装用 `--no-build-isolation`（脚本落在 env Scripts，不在 PATH，用 `python -m exocore_tts.server` 或全路径调用） | 计划要求；`httpx` 为 TestClient 传输层 | 无 |
| h | `ModelRuntime.run()` 增加 worker 再入守卫（thread-local 标记 → `RuntimeError`，测试 `test_reentrant_run_from_the_worker_is_refused_instead_of_hanging`） | 再入会等在自己阻塞的队列上 → 永久挂死；错误用法应当可诊断 | 无（内部 API 加固，非契约面） |

**未记录的隐式偏差**：无。`casting.py`、`voices.py`、三条冻结 WAV 与 `voice.json` 全部未触碰（`git show --stat 668b463` 可核）。

---

## 6. 本 checkpoint 之外（下一步的明确边界）

- **Checkpoint B（§8 第 4 步）**：`voxcpm.py` 低层入口抽取、`backends/voxcpm2.py`、casting 复用不回归、真机短句 smoke（cold→loading→ready→WAV）。
- **delivery 产品门（§3 / §8 第 5 步）**：Alicia 人耳裁决；通过则 mapping 固定进 Vox backend，不通过则固定 `delivery_unsupported`。
- **§8 第 6–7 步**：README/`voices.py` 旧 Django 镜像说明/`AGENTS.md` 收尾、真机冒烟全流程。
- A 阶段**不会**出现：真实模型加载、GPU 占用、`voxcpm`/`torch` import（daemon 路径上唯一的重量级 import 在 `casting.py`，且是惰性的）。

---

## 7. 真机 smoke 记录（A 阶段，loopback，无模型）

命令：`EXOCORE_TTS_PORT=8791 python -m exocore_tts.server`（另跑 token 模式与门禁用例）。

```text
[1] GET /health                       -> 200 {"status":"ok","state":"cold"}
[2] POST /tts sandro_v1               -> 503 {"error":"engine_unavailable"}   # voxcpm2 未接（B）
[3] POST /tts + 未声明字段 seed        -> 422 {"error":"invalid_request"}
[4] GET /voices                       -> 404 {"error":"invalid_request"}      # M2 不提供该端点
[5] EXOCORE_TTS_HOST=0.0.0.0          -> 拒绝启动, exit=2
    "refusing to bind non-loopback host '0.0.0.0': loopback is the daemon's network boundary"
[6] EXOCORE_TTS_HOST=localhost        -> 拒绝启动, exit=2
    "EXOCORE_TTS_HOST must be a literal loopback address (127.0.0.1 or ::1), got 'localhost'"
[7] 带 EXOCORE_TTS_TOKEN=s3cret：
    /health 无 bearer                 -> 401 {"error":"unauthorized"}
    /tts 无 bearer                    -> 401 {"error":"unauthorized"}   # auth 先于路由/校验（空 body 也 401，见测试）
    /health 带 Bearer s3cret          -> 200 {"status":"ok","state":"cold"}
    /tts 带 Bearer s3cret             -> 503 {"error":"engine_unavailable"}
```

---

## 8. 已知边界与建议的对抗点

**边界**

1. 未接真实引擎：A 的证据只覆盖 HTTP/编排/生命周期；Vox 参数、参考音频缺失 → 503 的**真机**证据在 B（A 已用 corrupt manifest + backend 主动抛 `EngineUnavailable` 两类用例钉住映射）。
2. `delivery` 只保证 wire 透传与显式拒绝；Vox 是否可安全实现 = B 的探针 + 人耳。
3. 结构校验是客观门槛，不含静音段/时长质量策略（§4.4 明确不做）。
4. `tests/test_server.py` 使用 `raise_server_exceptions=False` 才能断言 500 错误体（Starlette 在 Exception handler 之后仍会向测试客户端 re-raise）；这是测试客户端行为，不是产品行为。

**建议对抗点（欢迎打回）**

1. 冷加载 + 并发 + 空闲卸载三件事叠加（把 `idle_unload_seconds` 调小、并发发请求、观察 `load_count`/`unload_count` 与 health 序列）。
2. `EXOCORE_TTS_MAX_TEXT_CHARS=1` 时发多段文本（应为 422 或单字符成功，不得越界）。
3. token 模式下同时发错字段与错 token（应 401，且 body 无 `detail`）。
4. 直接调用 `service.synthesize(text=123, ...)` / `voice_key=None`（应 `InvalidRequest`，不得 500）。
5. 以 `runtime.run()` 在 worker 线程内再入：应得到即时 `RuntimeError`（用例 `test_reentrant_run_from_the_worker_is_refused_instead_of_hanging`），不得挂死。
