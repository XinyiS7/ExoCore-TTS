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
- **M2（部分落地，等待独立验收）**：`POST /tts` + `GET /health` 守护进程。已提交：CP-A `668b463`（非 GPU 地基：契约层 / 编排 / 单 worker 生命周期 / 分段 / `fake` 替身 / 启动门禁）与 CP-B `a82f83a`（`voxcpm.py` 共用低层入口 + `voxcpm2` 真推理 + casting 不回归）；非 GPU 测试 151/151。生命周期已裁决并实现：**惰性加载**（启动不加载模型）、**空闲卸载默认 1800 s**（`EXOCORE_TTS_IDLE_UNLOAD_SECONDS`，`0` 禁用，可配置）。剩余：delivery 人耳探针、`voices.py` 旧 Django 镜像说明、真机冒烟（en/de、多段、idle 卸载、load 失败）；证据见 `Plan/0003_checkpoint_A_acceptance.md` / `Plan/0003_checkpoint_B_acceptance.md`。
- **M3**：ExoCore 适配器改 key-based + `base_url` 配置化（跨仓，计划落 `ExoCore/Plan/`）。
- **M4**：工具侧 `send_voice_msg` 与前端接线。
