# 0001 — 声音工厂骨架与选角台（M1）

> **性质**：施工记录（简短行为备忘）。M1 只落地工具链，不含 HTTP 守护进程。
> **日期**：2026-09-26　**署名**：deepseek-flash / Ecki

## 目标

把"选声音"这条路先打通，并且让工厂从第一天就是独立仓（不再重演 Runtime 先进伞仓再剥离）。

## 行为 / 范围

- 新建独立仓 `ExoCore-TTS`，本地挂 `ExoCore_Project/ExoCore-TTS/`；伞仓 `.gitignore` 登记、`AGENTS.md`/`Agent` 与 `docs/repo_topology.md` 同步。
- **声音资产库** `src/exocore_tts/voices.py`：`voices/<key>/reference.wav` + `voice.json`；key 校验、防覆盖、原子写 manifest。
- **选角台** `src/exocore_tts/casting.py` + `tools/cast.py`：`design` / `clone` / `pick` / `list` 四个子命令。
  - 盲听（文件名只有编号，描述与参数在 `manifest.json`）；
  - 可复现（cfg / timesteps / seed / 实际送入模型的文本全部记录）；
  - 可续跑（manifest 每条候选后落盘，重跑跳过已完成）；
  - 省 GPU（模型一批只加载一次；`--dry-run` 不加载）。
- 不做：HTTP daemon（M2）、Django 侧接线（M3）、torch 相关的单元测试（那属于 M2 的真冒烟）。

## 验证

- `python.exe -m unittest discover -s tests -v`（在 `voxcpm_runtime` 环境）全绿（28 项）；覆盖计划展开、模式参数互斥、字数 guard、manifest 增量与原子性、pick 冻结链路、资产库读写与防覆盖、WAV 原子落盘格式推断回归。
- `tools/cast.py design ... --dry-run` 能在不加载模型的情况下打印完整计划。
- **真机合成冒烟已完成（2026-09-26）**：`candidates/round1` 一批 64 条候选全跑通——冷启动 41.7s，合成合计 6.2 分钟，平均 RTF 2.10，峰值显存 5458MB。
- 冒烟途中确认的两个环境事实（已写进 README）：
  1. `soundfile` 无法从 `.wav.tmp` 推断容器格式 → 已改为显式 `format="WAV"` 并补回归测试；
  2. 加载权重需要 ≥ ~8GB 空闲 commit，本机页面文件只有 3GB，实测 4 次加载失败 2 次 → M2 开工前建议先把页面文件提到 16GB。
