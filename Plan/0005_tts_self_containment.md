# 0005 — TTS 自包含：不再隐式读兄弟仓 `ExoCore/.env`

> **状态**：IMPLEMENTED（5 个提交，**未推送**）——builder 证据见 §8，待 pane 5（Solaire · Acceptance）独立复核。
> **施工边界**：不改 ExoCore / Runtime / Desktop；**不做真实 provider/synthesis 调用、不重启服务、不推送**；
> 既有 owner 脏项（`.gitignore`、`.env`、`ai_studio_code.py` 及其它 scratch）一律原样保留、**不得暂存**。

## 1. 问题

`config.dotenv_path()` 的默认值是 `repo_root().parent / "ExoCore" / ".env"`（`config.py:132`）：
**本服务默认去兄弟仓读凭据**。daemon 的 `gemini` backend 经 `cloud.read_api_key()` 继承同一默认，
因此"注入为空"时会静默回退到另一个 project 的旧 key，表现为 404 voice-not-found
（`Plan/0004_checkpoint_G2_evidence.md` §9 的 incident）。这与本仓边界冲突：TTS 对外只通过 loopback HTTP
说话，凭据必须来自**本仓自己的 `.env`** 或**显式注入**。

## 2. Frozen contract（逐条实现）

1. `config.dotenv_path()` 默认 = `repo_root() / ".env"`。`EXOCORE_TTS_DOTENV` 仍是**只显式**覆盖，
   **无链式回退**：设置了就用它，缺失即失败，不回落默认路径。
2. 解析顺序（daemon 与工具共用**同一实现**）：
   a. 进程环境 `GEMINI_API_KEY`（strip 后非空即胜出）；
   b. 否则读"已解析的 .env"（显式 `dotenv` 参数 > `EXOCORE_TTS_DOTENV` > 本仓默认），
      同一文件内先 `GEM_TTS_KEY`（canonical）后 `GEMINI_API_KEY`（legacy），各取**首个非空**值。
3. 行解析必须安全：兼容 CRLF、单/双引号（一层成对）、`=` 两侧空白、空值（视作未设置）、忽略注释与无关行；
   名为**精确匹配**（`GEM_TTS_KEY_OLD`、`MYGEM_TTS_KEY` 不得误配）。不引入 `export`/变量展开等额外语义。
4. 任何路径都不打印/记录 key 值；错误信息只含变量名与路径。
5. `verify.read_api_key()` 与 daemon 读取**语义与错误类型统一**，不再是第二份解析器。

## 3. 实现点

| 文件 | 改动 |
|---|---|
| `src/exocore_tts/config.py` | `dotenv_path()` 默认改本仓 `.env`；docstring 重写（自包含声明）；模块 docstring 的 env 清单同步 |
| `src/exocore_tts/cloud.py` | 新增 `dotenv_value(path, names)` 纯路径解析器（无环境读）；`read_api_key()` 按 §2 重写；模块 docstring 更新 |
| `src/exocore_tts/verify.py` | `read_api_key()` 变薄委托（同语义、同 `CloudError`） |
| `README.md` / `tools/render_reference.py` / `tools/verify_audio.py` | 措辞改为本仓 `.env` + 显式注入 |

## 4. 测试（新增 `tests/test_self_containment.py`）

- **Headline 回归（主证）**：伪造布局 `tmp/ExoCore/.env` 内含**看起来可用**的 key、且本仓 `.env` 缺失
  （patch `config.repo_root`）→ `read_api_key()` 必须**失败**，且错误串中**不出现**兄弟仓 key 值。
- 优先级：进程 env > 文件；同文件 canonical > legacy（含"legacy 在前、canonical 在后"）；显式 `dotenv` > `EXOCORE_TTS_DOTENV` > 默认。
- `EXOCORE_TTS_DOTENV` 显式独占：指向缺失文件 + 本仓 `.env` 有 key → 仍失败（无回退）。
- 缺失路径 / 无目标变量 / 空值 → 明确失败（消息含路径与变量名，不含值）。
- 解析矩阵：CRLF、单/双引号、内层空白、`=` 两侧空白、`KEY=` / `KEY=""`、注释、无关行、近似名、重复键取首个非空。
- `dotenv_path()` 默认 = `<repo>/.env` 且**不在父目录下**。
- 既有 `tests/test_cloud.py`（保持通过）与 `tests/test_verify.py::KeyTests`（错误类型统一为 `cloud.CloudError`）按新语义更新。

## 5. 门禁

- 全量：`E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v`（无网络、无真实调用）
- 静态：`ruff check src tests tools`（若环境有）、`git diff --check`、行尾复查（防 CRLF 污染）
- 提交：作者 `deepseek-flash <agent@exocore.local>`（沿用既有惯例）；原子提交顺序 =
  memo → config 默认 → cloud 解析器（含 headline 回归）→ verify 统一 → README/工具文档
- **不做**：真实 provider/synthesis 调用、服务重启、推送；不暂存任何既有脏项。

## 6. Erratum（历史证据修正索引 — 冻结文件不改写）

| 位置 | 旧陈述 | 修正 |
|---|---|---|
| `Plan/0002_register_and_sandro_v1_freeze.md:31` | key 从隔壁 `ExoCore/.env` 读 | 默认改本仓 `.env`；跨 project 需显式 `EXOCORE_TTS_DOTENV` |
| `Plan/0003_tts_daemon.md:279` | sibling fallback 仅保留给云端制作/抽检工具 | 该 fallback 整体移除；daemon 与工具同语义 |
| `Plan/0004_cp_g2_runbook.md:13,16,19,35,95` | 记录注入 `ExoCore/.env` 的 `GEMINI_API_KEY`；描述静默回退 | 注入手法与 key 门禁仍然有效；"静默回退"在新语义下不可能发生 |
| `Plan/0004_checkpoint_G2_evidence.md:125` | incident：空注入静默回退旧 key | 由本次改动从结构上消除（该记录本身是问题描述，保持原样） |
| `Plan/0004_gemini_production_renderer_acceptance.md:98,141` | "key 来源并非不一致……刻意设计" | 保留其事实；跨 project 选择今后必须显式，不再是默认行为 |
| `README.md:111,262` | 默认读隔壁 `ExoCore/.env` | **就地更新**（活文档，非冻结证据） |

## 7. 交付物 / 停止条件

变更提交（**不推送**）＋ 全量测试与门禁输出 ＋ 精确基线脏项清单 → 交 pane 5（Solaire · Acceptance）复核；到点即停。

## 8. 施工结果（builder 证据 — 待独立复核，非 verdict）

- **提交（原子，不推送）**：`1ac6e1d` memo → `d1ebd09` config 默认本仓 → `5996483` cloud 解析器
  （含 headline 回归与解析矩阵）→ `a3d38a7` verify 统一 → 文档（README + 两个工具 docstring）→ 本条记录。
- **测试**：全量 **215/215 OK**（改动前 193；净增 22 = 新文件 20 例 + `test_verify` key 用例 +2）。
  新增文件：`tests/test_self_containment.py`（路径默认 3 / 兄弟仓隔离 3 / 优先级 6 / 解析矩阵 8）。
- **headline 回归**：伪造 `tmp/ExoCore/.env`（内含可用形参的 key）+ 本仓 `.env` 缺失 → `read_api_key()`
  失败，且错误串不含兄弟仓 key 值（正反两向断言）。
- **门禁**：`git diff --check` 干净；逐文件无 CRLF；`compileall` OK；`ruff` 本机环境未安装（已记录，未跳过其它静态检查）。
- **约束遵守**：无真实 provider/synthesis 调用、无服务重启、无推送；owner 脏项（`M .gitignore`、
  `?? ai_studio_code.py`、`.env`）全程未动、未暂存。
- **冻结证据**：`Plan/0002`、`0003`、`0004*` 历史文档一字未改（修正索引见 §6）。
