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
- **M2（已通过独立实现验收 `f9be708`）**：`POST /tts` + `GET /health` 守护进程。已接受实现：CP-A `668b463`（非 GPU 地基：契约层 / 编排 / 单 worker 生命周期 / 分段 / `fake` 替身 / 启动门禁）、CP-B `a82f83a`（`voxcpm.py` 共用低层入口 + `voxcpm2` 真推理 + casting 不回归）、Amendment 01 `b8930c3`（分段改为脚本加权单位预算：中文 ≤40 字/段、纯拉丁 ≤120 字符/段；A2 标点优先回退）与 `voices.py` 文档清理 `fab2d9c`；非 GPU 测试 159/159 已由 `f9be708` 独立复现；真机收尾数字仍是 builder 证据（verdict 引用接受）；Gate-0 的 PASS 只覆盖契约 / 文档一致性，与实现 verdict 不混读；非阻塞后续项见 `Plan/0003_m2_independent_acceptance.md`。生命周期已裁决并实现：**惰性加载**（启动不加载模型）、**空闲卸载默认 1800 s**（`EXOCORE_TTS_IDLE_UNLOAD_SECONDS`，`0` 禁用，可配置）。**delivery 产品门已收口**：探针 `0a7f19c` 未通过（Alicia 听判），本地 `voxcpm2` 固定 `supports_delivery=False`、非空 `delivery` 返回 `422`。真机冒烟步骤 3–8 已执行（证据 `Plan/0003_m2_smoke_closure_evidence.md`，`a345ef0`）；证据见 `Plan/0003_checkpoint_A_acceptance.md` / `Plan/0003_checkpoint_B_acceptance.md` / `Plan/0003_delivery_probe_result.md`。
- **M3**：ExoCore 适配器改 key-based + `base_url` 配置化（跨仓，计划落 `ExoCore/Plan/`）。
- **M4**：工具侧 `send_voice_msg` 与前端接线。
