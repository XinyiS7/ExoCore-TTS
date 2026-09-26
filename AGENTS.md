# ExoCore-TTS Agent Guide

本仓库是 ExoCore 的**声音工厂**：对外只暴露一个 TTS 端口，内部把本地/云端合成后端换掉。必须与 Django 保持独立。

## Repository

- 独立仓库：`git@github.com:XinyiS7/ExoCore-TTS.git`，分支 `main`。
- 本地通常落在 `ExoCore_Project/ExoCore-TTS/`（嵌套独立克隆，伞仓不 track 它）。
- 跨仓计划（本仓 + Django 联动）落 `ExoCore/Plan/`，不在本仓另起一份；本仓自己的独立事项写 `Plan/`。

## Boundaries

- 绝不 import Django / ExoCore / 任何主仓 Python 包，也不碰 Django 的数据库。两边只通过 loopback HTTP 说话。
- 只绑 loopback 地址。
- **契约里永远不出现宿主机路径**：ExoCore 只给 `voice_key`。声音怎么被造出来是本仓的内部事务。
- 声音资产（`voices/<key>/`）由本仓拥有。`reference.wav` 是选角成果，覆盖必须显式（`--force`）。
- 重量级依赖（torch / voxcpm）只允许存活在本服务自己的 conda 环境 `voxcpm_runtime`；**绝不**写进 `ExoCore/requirements.txt`。
- 显存纪律：3060 Ti 可用约 6.8GB，实测 94 字台词峰值约 6.4GB。单条台词有 120 字 guard，不要并发多条合成。
- **提交内存纪律**：加载权重需要 ≥ ~8GB 空闲 commit；本机页面文件只有 3GB，加载会间歇性失败（`OSError 1455` / segfault）。遇到这种情况先看 README 的「环境」段，不要怀疑代码。
- 严禁空 catch 或伪成功降级：失败要如实暴露成错误码，不要假装成功。

## Commands

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m pip install -e .          # 只装在这个环境
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py --help
```

Python 源码统一使用 ASCII 双引号；文本基线见 `.gitattributes` / `.editorconfig`（文本 LF、音频二进制）。

## Milestones

- **M1（已完成）**：仓库骨架 + 声音资产库（`src/exocore_tts/voices.py`）+ 选角台（`src/exocore_tts/casting.py`、`tools/cast.py`）。
- **M2**：`POST /tts` + `GET /health` 守护进程；`backends/` 下先有 `fake`（契约测试用）再有 `voxcpm2`（真推理）。开工前必须先裁决模型常驻/空闲卸载策略。
- **M3**：ExoCore 适配器改 key-based + `base_url` 配置化（跨仓，计划落 `ExoCore/Plan/`）。
- **M4**：工具侧 `send_voice_msg` 与前端接线。
