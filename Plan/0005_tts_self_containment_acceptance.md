# 0005 — TTS 自包含：独立验收记录（docs-only closeout）

> **verdict**：**PASS — RELEASE CONDITION 满足**（docs-only 收口）。
> **验收 owner**：Solaire · Acceptance（pane 5）；**builder**：Ecki（deepseek-flash）。
> **接受范围**：`origin/main 62cefe5..bb735b9`，**7 条线性未推提交**。
> **本文件**：不改生产代码 / 测试 / `Plan/0002`–`0004` 冻结证据 / `AGENTS*`；**不含任何 key 值或片段**。

## 1. 冻结契约（Plan/0005 §2 — 逐条被验收）

1. `config.dotenv_path()` 默认 = `repo_root() / ".env"`；`EXOCORE_TTS_DOTENV` 显式独占、**无链式回退**。
2. 解析顺序：进程 `GEMINI_API_KEY`（非空即胜）→ 已解析的 `.env`（显式 `dotenv` > `EXOCORE_TTS_DOTENV` >
   本仓默认）内 **`GEM_TTS_KEY` → legacy `GEMINI_API_KEY`**。
3. 行解析安全：CRLF、单/双引号、`=` 两侧空白、空值视作未设置、注释/无关行忽略、名字精确匹配。
4. 任何路径不打印/记录 key 值；错误信息只含变量名与路径。
5. `verify.read_api_key()` 与 daemon **同语义、同错误类型**（`cloud.CloudError`），不再是第二份解析器。

## 2. 独立门禁（验收方复跑）

| 门禁 | 结果 |
|---|---|
| source / spec / security 复核 | **PASS** |
| 全量 unittest | **215/215** |
| 聚焦自包含套件（`tests/test_self_containment.py`） | **20/20** |
| verify + cloud 相关 | **33/33** |
| `compileall` / `git diff --check` | **PASS** |
| `.env` 状态 | 被忽略、未被跟踪；`git add .env` 被忽略规则拒绝 |
| 工作区 | 仅 `?? ai_studio_code.py`（owner 既有未跟踪文件，未触碰） |
| 静态 lint | `ruff` 在本机环境**未安装、也非本仓声明依赖** → 未作为门禁执行，也**未以任何方式声称通过**（记录在案） |

## 3. headline 回归（验收确认）

伪造 `tmp/ExoCore/.env`（内含可用形参的 key）且本仓 `.env` 缺失 → `read_api_key()` **必失败**，
且错误串**不含**该 key 值；本仓 `.env` 存在时以它为准。§4 的 20 例族（路径默认 3 / 兄弟仓隔离 3 /
优先级 6 / 解析矩阵 8）全部通过。

## 4. 运行期零成本复核（post-restart evidence）

清空环境变量后启动 daemon（PID 12616）：`/health` = **200 / cold**；解析路径选中 **`repo_root/.env`**
（仅记录 key **长度**级事实，**值从未打印**）；**无兄弟仓选择**；**无任何 provider/synthesis 调用**
（该复核进程由验收方处置）。

## 5. 接受范围构成（7 条，未推送）

`1ac6e1d` memo ｜ `d1ebd09` config 默认本仓 ｜ `5996483` cloud 唯一解析器 + headline 回归 ｜
`a3d38a7` verify 统一 ｜ `f5abe2a` README/工具文档 ｜ `c99b478` 施工记录 ｜
`bb735b9` security（`.gitignore` 采纳根 `.env`）+ 状态行更正（收口修复）。

## 6. 边界声明

- 未改动：生产代码（§3 范围之外的部分）、测试、`Plan/0002`/`0003`/`0004*` 冻结证据、`AGENTS*`。
- 推送未发生，由 Alicia / 验收方另行决定。
- 本文件只记录长度级/结构性事实，不含 key 值与其片段。
