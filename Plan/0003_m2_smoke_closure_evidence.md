# 0003 — M2 §9.2 收尾真机冒烟 Builder 证据（步骤 3–8）

> **Builder evidence, awaiting independent acceptance; not a verdict.**
> 本文件是**建造方证据**：只陈述可复现的观测事实与命令，**不含任何验收判定**（不写 PASS/FAIL，不代表 CP-A / CP-B / M2 已获验收）。
> **日期**：2026-09-27　**执行/记录**：Ecki（pane 3）　**授权**：Solaire（经 Alicia 转达；限定"仅此一份 tracked 文档、不碰其它文件、不推送"，初版留工作区待 QC）
> **状态**：已提交 `a345ef0`（单文件提交，未推送）；本文件是建造方证据，不是验收裁决。

---

## 0. 范围、基线与不变量

**覆盖**：`Plan/0003_tts_daemon.md` §9.2 的**步骤 3–8**（真机，RTX 3060 Ti）+ `voices.py` 文档清理（§8 第 6 步的本仓部分）。

**基线提交**（本仓 `main`，全部未推送）：

| 提交 | 角色 |
|---|---|
| `668b463` | CP-A 非 GPU 地基（契约层 / 编排 / 单 worker 生命周期 / 分段 / fake 替身 / 启动门禁） |
| `a82f83a` | CP-B 真实 Vox 路径（`voxcpm.py` 共用低层入口 + `backends/voxcpm2.py`） |
| `0a7f19c` | delivery 探针结果（失败分支落库，行为未变） |
| `fab2d9c` | `voices.py` 旧 Django 镜像说明清理（见 §9） |

**本次未触碰**：`Plan/0003_tts_daemon.md`、`README.md`、`AGENTS.md`、生产代码、测试、两份 checkpoint 验收包、delivery 结果档。

**证据工件**：`candidates/m2_smoke_closure/`（**本地保留、被 `.gitignore` 覆盖**——`candidates/` 整目录；它们是**本地工件，不是 canonical 证据**）。本文档把决定性数字内嵌，不依赖这些文件仍存在。

**步骤 1–2（cold 启动 / 加载期间 health 可用）**：证据见 `Plan/0003_checkpoint_B_acceptance.md` §5（**引用，不复制**）。

---

## 1. 环境与复现方式

```text
机器      : Windows 11，RTX 3060 Ti（8192 MiB；桌面基线占用 835–1071 MiB）
解释器    : E:/Miniconda3/envs/voxcpm_runtime/python.exe
模型缓存  : 本地 HF cache 完整；守护进程以 HF_HUB_OFFLINE=1 启动（离线、不触发下载）
守护进程  : python -m exocore_tts.server，EXOCORE_TTS_PORT=8791，未配置 token
声线      : sandro_v1 / sandro_en_v1 / sandro_de_v1（资产内 cfg 3.5 / timesteps 16）
请求体    : candidates/m2_smoke_closure/body_*.json（本地工件）
```

两个守护进程实例（互不重叠，跑完即停，显存回基线）：

```bash
# 会话 1（步骤 4、5；idle 阈值取默认 1800s）
HF_HUB_OFFLINE=1 EXOCORE_TTS_PORT=8791 python -m exocore_tts.server

# 会话 2（步骤 7、6、3；idle 阈值显式 60s）
HF_HUB_OFFLINE=1 EXOCORE_TTS_IDLE_UNLOAD_SECONDS=60 EXOCORE_TTS_PORT=8791 python -m exocore_tts.server
```

通用请求与校验：

```bash
curl -s -o out.wav -w "http=%{http_code} total=%{time_total}s bytes=%{size_download}\n" \
  -X POST http://127.0.0.1:8791/tts -H 'content-type: application/json' \
  --data-binary @candidates/m2_smoke_closure/body_zh.json

# 帧数/静音段校验（Windows 路径注意用 cygpath -w 转换）
python -c "import numpy as np,soundfile as sf; d,r=sf.read('out.wav'); print(len(d),r,bool(np.isfinite(d).all()))"

# 语言配对内容抽检（云端 ASR，两条 passes）
python tools/verify_audio.py --clip <file>.wav --text "<该次请求的正文>" --language zh|en|de
```

---

## 2. 步骤 3：并发冷请求（本次补的真机项）

**起始状态**：`/health` = `{"status":"ok","state":"cold"}`（模型已在上一步空闲卸载）。

**动作**：两条 POST 同时发出（不同正文，同一 `sandro_v1`）。

| 观测 | A 条（`把窗户关上。`） | B 条（`把灯也关了。`） |
|---|---:|---:|
| HTTP | 200 | 200 |
| 端到端 | 23.21 s | 31.27 s |
| 响应体 | 184,364 B | 245,804 B |

**状态转移与串行性（守护进程日志）**：

```text
10:55:34.170  engine voxcpm2 ready in 16.97s          <- 整段并发中只出现一次加载
10:55:40.404  segment 1/1 chars=6 frames=92160  infer=6.23s rtf=3.247
10:55:40.405  render ok ... wav_bytes=184364 wall=23.21s
10:55:48.473  segment 1/1 chars=6 frames=122880 infer=8.07s rtf=3.152
10:55:48.474  render ok ... wav_bytes=245804 wall=31.12s
```

结论性观测（非判定）：**一次加载、推理严格串行、两条各自完整返回**；ASR 两条各两遍均 100%（正文与文件一致）。

---

## 3. 步骤 4：多段拼接（两次请求，帧数逐帧精确）

| | 请求 1 | 请求 2 |
|---|---|---|
| 正文长度（strip 后） | 273 字符（中英混合） | 417 字符（中英混合） |
| 分段数 | 5 | 8 |
| 逐段帧数（48 kHz） | 261120 / 284160 / 837120 / 399360 / 599040 | 261120 / 337920 / 860160 / 368640 / 591360 / 583680 / 337920 / 284160 |
| 段内帧和 | 2,380,800 | 3,624,960 |
| 段间 gap（`GAP_MS=250` → 12,000 帧/个） | 4 × 12,000 = 48,000 | 7 × 12,000 = 84,000 |
| **等式** | 2380800 + 48000 = **2,428,800** | 3624960 + 84000 = **3,708,960** |
| WAV 实测帧数 | 2,428,800 ✓ | 3,708,960 ✓ |
| 响应体（bytes） | 4,857,644 = 44 + 2 × 2,428,800 ✓ | 7,417,964 = 44 + 2 × 3,708,960 ✓ |
| 时长 | 50.60 s | 77.27 s |
| 端到端 wall | 184.18 s | 245.80 s |

其他观测：48 kHz 单声道 PCM_16；`finite=True`；峰值 0.9975 / 0.9976；**所有段间 gap 区间逐样本 == 0.0**；ASR 两遍覆盖两段的全部句子（含英文部分）。

---

## 4. 步骤 5：en / de 声线

| 声线 | 正文 | 端到端 | 响应体 | ASR（语言配对，两遍） |
|---|---|---:|---:|---|
| `sandro_en_v1` | `Close the window, the wind is picking up out there.` | 34.52 s | 430,124 B | 100% 干净 |
| `sandro_de_v1` | `Mach das Fenster zu, der Wind wird stärker.` | 37.57 s | 399,404 B | 100% 干净 |

**Alicia 听判范围**：ASR 只证明"语言/内容正确"；**声音身份（是否仍是三位各自的 Sandro）由 Alicia 人耳判断**——文件为 `candidates/m2_smoke_closure/sandro_en_short.wav`、`sandro_de_short.wav`（本地工件）。该听判是可选的人工项，未包含在本文件的数字证据内。

---

## 5. 步骤 6：空闲卸载（阈值 60 s）与临界点请求

会话 2 以 `EXOCORE_TTS_IDLE_UNLOAD_SECONDS=60` 启动。

**临界点请求**：在上一次请求完成后的 **idle = 55 s** 提交（阈值 60 s，监控线程每 15 s 一跳）→ HTTP 200，端到端 7.53 s，在 **idle = 63 s** 完成。

观测：该请求跨过阈值执行期间**未被卸载打断、也没有发生重载**（该守护进程日志中此窗口内没有第二条 `ready in`）；请求结束后 `/health` = `ready`。

**随后空闲**：

```text
10:54:55.823  engine voxcpm2 evicted after 60s idle
10:55:04      /health = {"status":"ok","state":"cold"}（轮询观测点）
```

**显存**：模型 ready 时 7511 MiB（含桌面占用）→ 卸载后 **1002 MiB**（回基线）。

---

## 6. 步骤 7：人为 loader 失败 + 同进程恢复

**手法**（不改代码、不联网）：把本地快照中的 `model.safetensors`（4,580,080,592 B）改名藏起；守护进程运行在 `HF_HUB_OFFLINE=1`，因此**不会**触发重新下载，而是直接判定快照不完整。

| 阶段 | 观测 |
|---|---|
| 起始 | `/health` = `cold` |
| 失败请求 | HTTP `503`，body `{"error":"engine_unavailable"}`；`/health` 随即仍为 `{"status":"ok","state":"cold"}` —— **未卡在 `loading`**，也**未把异常文本泄漏到响应**（日志内才含完整原因：offline 模式下快照缺 `model.safetensors`） |
| 恢复 | 权重文件还原（大小一致 4,580,080,592 B） |
| 第二次请求（**同进程**） | HTTP 200；`engine voxcpm2 ready in 20.55s`；`render ok ... segments=1 wav_bytes=184364 wall=29.00s` |

结论性观测（非判定）：加载失败后状态回到可重试的 `cold`，且同一进程内的下一次请求可以通过一次新的加载成功完成。

---

## 7. 步骤 8：计时 / RTF / 释放

**冷加载观测（本次，三次）**：**24.70 s / 20.55 s / 16.97 s**。

> ⚠️ **口径**：以上是**离线 + 本地 HF 缓存已就绪**的观测，**不得**用来替换 CP-B 记录的 **53.56 s 加载 / 73.88 s 首次冷请求**（那一次包含联网 revision 校验与不同磁盘缓存状态），也不改变 `Plan/0003_tts_daemon.md` §2.4 对调用方 **≥ 90 s** transport timeout 预算的要求。

**逐段推理（会话 1 样例，RTF = infer / audio）**：

| 段 | 字符数 | 帧数 | infer | RTF |
|---|---:|---:|---:|---:|
| 1/5 | 17 | 261,120 | 19.41 s | 3.567 |
| 2/5 | 80 | 284,160 | 18.36 s | 3.101 |
| 3/5 | 68 | 837,120 | 53.76 s | 3.083 |
| 4/5 | 58 | 399,360 | 26.80 s | 3.222 |
| 5/5 | 49 | 599,040 | 41.13 s | 3.296 |

单段短句样例：6 字符两次并发分别 6.23 s（RTF 3.247）与 8.07 s（RTF 3.152）；`把窗户关上。` 端到端 7.53–29.00 s（后者含一次冷加载）。整体 RTF 落在 **3.0–3.6**。

**多段端到端**：5 段 184.18 s；8 段 245.80 s。

**释放**：idle 卸载后显存回基线（1002 MiB）；守护进程停止后无监听套接字，显存保持基线（835–836 MiB 观测）。

---

## 8. 步骤 1–2（引用）

cold 启动（无模型/无显存占用）、第一个冷请求等待加载后直接返回音频、加载期间 `/health` 可响应且报 `loading`：见 `Plan/0003_checkpoint_B_acceptance.md` **§5**（含 53.56 s / 73.88 s 原始观测与 WAV 复验）。本文件不复刻该记录。

---

## 9. `voices.py` 文档清理（`fab2d9c`，与冒烟无关的独立项）

- 变更范围：**仅 docstring**（`+3 / −8`）：删除旧文"Mapping to ExoCore's `VoiceProfile`"的字段镜像说明，改为"manifest 是声音的唯一权威；其它位置的绑定只存 key，不得镜像为第二真相"。
- 行为影响：无（无代码路径改动）；该提交处全量非 GPU 测试 **151/151 OK**。
- 该提交之外：本轮无其它代码改动。

---

## 10. 复现清单（完整命令序列）

```bash
# 会话 1：步骤 4 + 5
HF_HUB_OFFLINE=1 EXOCORE_TTS_PORT=8791 python -m exocore_tts.server        # 终端 A
curl -s -X POST http://127.0.0.1:8791/tts -H 'content-type: application/json' \
     --data-binary @candidates/m2_smoke_closure/body_multi.json      -o multi5.wav
curl -s -X POST http://127.0.0.1:8791/tts -H 'content-type: application/json' \
     --data-binary @candidates/m2_smoke_closure/body_multi_long.json -o multi8.wav
curl -s -X POST http://127.0.0.1:8791/tts -H 'content-type: application/json' \
     --data-binary @candidates/m2_smoke_closure/body_en.json         -o en.wav
curl -s -X POST http://127.0.0.1:8791/tts -H 'content-type: application/json' \
     --data-binary @candidates/m2_smoke_closure/body_de.json         -o de.wav

# 会话 2：步骤 7 -> 6 -> 3
HF_HUB_OFFLINE=1 EXOCORE_TTS_IDLE_UNLOAD_SECONDS=60 EXOCORE_TTS_PORT=8791 \
     python -m exocore_tts.server                                  # 终端 A（先藏权重再启动）
SNAP="$HOME/.cache/huggingface/hub/models--openbmb--VoxCPM2/snapshots/<rev>"
mv "$SNAP/model.safetensors" "$SNAP/model.safetensors.hidden"      # 步骤 7 失败注入
curl -s -o /dev/null -w "%{http_code}\n" -X POST ... --data-binary @.../body_zh.json   # 期望 503
mv "$SNAP/model.safetensors.hidden" "$SNAP/model.safetensors"      # 还原（务必执行）
curl -s -o ok.wav -w "%{http_code}\n"    -X POST ... --data-binary @.../body_zh.json   # 期望 200
# 步骤 6：等至 idle≈55s 发请求（不被打断），再等过阈值（/health 转 cold、显存回落）
# 步骤 3：/health 为 cold 时并发两条 POST，检查日志仅一条 "ready in" 且 segment 行不交错

# 抽检
python tools/verify_audio.py --clip en.wav --text "Close the window, the wind is picking up out there." --language en
python tools/verify_audio.py --clip de.wav --text "Mach das Fenster zu, der Wind wird stärker." --language de
```

本地工件（gitignored，非 canonical）：`candidates/m2_smoke_closure/` 下 `FACTS.md`、`body_*.json`、`multi_5seg_273chars.wav`、`multi_8seg_417chars.wav`、`concurrent_A/B.wav`、`sandro_en_short.wav`、`sandro_de_short.wav`。

---

## 11. 已知边界（本文件不做结论）

1. **delivery 产品门**不在本文件范围；其收口见 `0a7f19c` / `Plan/0003_delivery_probe_result.md`。
2. **长句"刺耳"渲染现象**（非注入独有）作为独立质量观察记录在 `Plan/0003_delivery_probe_result.md` §5。
3. **声音身份听判**（en/de、以及多段长文）属 Alicia 的人耳范围，未计入本文件的数字证据。
4. 本文件**不声明** CP-A / CP-B / M2 的验收结论；状态行与文档口径由 Solaire / pane 7 处置。
5. 本文件已提交 `a345ef0`、**未推送**；除状态文字外，证据内容自核验后未变更。
