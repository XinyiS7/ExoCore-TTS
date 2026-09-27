# CP-G2 runbook — 真机执行清单（+6 轮）

> 依据：`Plan/0004_gemini_production_renderer_acceptance.md` Addendum A2（裁决 `5731d39`）；
> 本轮预算 = **+6（总 ≤14，执行时从 9/14 起算）**；任一失败即停、不烧矩阵。

## 1. Key 来源（唯一口径；这是 Solaire 点名的 Minor）

**规则：用"拥有目标声线的那个 project"的 key。** 两个记录看似不一致，其实都是有意为之：

| 用途 | 目标声线 | key 来源 | 为什么 |
|---|---|---|---|
| 生产渲染（矩阵 + A/B） | `voice_oj0e4iyst73a`（'Ale 2.5 2'） | `ExoCore-TTS/.env` 的 **`GEM_TTS_KEY`** | 该 project 拥有这条声线（`voices.get` 双向验证） |
| C 探针（replacement，仅诊断） | `voice_nnvw5qprqmz7` | `ExoCore/.env` 的 `GEMINI_API_KEY` | probe #4 就是要用"另一 project 的 API 创建声线"做对照组 |

**变量名差异**：仓库契约只认 `GEMINI_API_KEY`（环境变量优先，其次 `EXOCORE_TTS_DOTENV` 或
`ExoCore/.env`）；Alicia 的文件用的是 `GEM_TTS_KEY`。执行时统一这样注入（**值不进命令行、不进日志**）：

```bash
export GEMINI_API_KEY="$(sed -n 's/^GEM_TTS_KEY=//p' .env | head -1 | tr -d '\r"' | tr -d "'")"
```

（daemon 与工具都按"环境变量优先"读取，因此无需改她的文件、无需动 `.env`。）

## 2. 前置（全部满足才能开跑）

- [ ] 基线风格扩展已实现并通过测试（schema round-trip + 默认/覆盖/缺省矩阵 + 无静默 fallback）
- [ ] Solaire 对扩展的放行；Alicia 的 +6 授权
- [ ] daemon 起在 `127.0.0.1:8769`，key 按 §1 注入，语音根 = 仓库 `voices/`

## 3. 执行序列（渲染编号从 9/14 开始）

| # | 内容 | 期望 | 失败处置 |
|---|---|---|---|
| 9 | **B 腿 / 控制实验**：zh 文本 + `delivery` = `"Style: "` + §6 片段逐字前缀（≤500 字符） | 200 + WAV ⇒ 短风格串能解析该声线（控制通过） | 立即停：不烧矩阵、保留日志、交回 Solaire/Alicia |
| 10 | **A 腿**：zh 文本，无 `delivery` → 资产基线风格默认 | 200 + WAV | 同上 |
| 11–14 | 矩阵：en / de / it / mixed（各自无 delivery → 基线默认） | 各 200 + WAV | 同上 |

合计 6 次（9–14）。其中 zh 的两条（#9 有覆盖 / #10 基线）即 **A/B 对照**，由 Alicia 听判：
「有没有可听的表演差异 / 指令没有被念出来 / 是不是 Ale 本人」。

## 4. 证据落盘

- 目录：`Plan/Acceptance_Probes/tts_gemini_cp_g2_render/`
- 每条：WAV + sha256 + 字节数 + 秒数 + 请求类别（语言/是否带 delivery）
- daemon 日志（`render ok … segments=1 wav_bytes=…` 行）同目录留档
- 汇总写回 `Plan/0004_checkpoint_G2_evidence.md`；**不记录 key、完整私密正文或 provider 原始异常**

## 5. 停止条件（任一命中即停）

- 任一请求非 200，或 WAV 结构校验失败
- provider 返回 not-found / permission / 配额类错误
- 需要"换一个声线才能继续"的任何情形（必须先交回，由 Alicia/Solaire 决定）
