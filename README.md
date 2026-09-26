# ExoCore TTS — 声音工厂

ExoCore 只认**一个** TTS 端口，这个仓库就是端口的那一侧。本地合成、云端合成、以后换任何引擎，都在**这一侧**解决——ExoCore 本体不受影响，也不需要跟着改。

```text
ExoCore (Django)                        本仓库 = 声音工厂
  │                                      │
  ├── POST /tts {text, voice_key, ...} ─►├── backend: voxcpm2   本地 3060 Ti
  │                                      ├── backend: cloud    （以后加，互不影响）
  ◄── 200 audio/wav（原始字节）───────────┤
```

## 不变量

1. **Django 只给 `voice_key`，永远不给宿主机路径。** 声音长什么样、参考音频放哪、用哪个引擎，都是这一侧的内部事务。换成云端时这条是关键——云端根本没有你本地的文件。
2. **响应保持 `audio/wav` 原始字节**（不是 JSON+URL）。ExoCore 现有适配器已经是按裸 WAV 解析的，契约这样定，接线时只改请求、不动响应解析。
3. **声音资产归本仓所有**：`voices/<key>/reference.wav` + `voice.json`。ExoCore 侧只保存"选了哪个 key"和展示名。
4. **绝不 import Django / 不碰 ExoCore 的数据库。** 两个进程通过 loopback HTTP 说话。
5. **重型依赖不出本仓**：torch / voxcpm 只活在 `voxcpm_runtime` 这个 conda 环境里，永远不进 `ExoCore/requirements.txt`。

## 端口契约（M2 实现，先冻结形状）

**`POST /tts`**

```json
{
  "text": "已经投影好的朗读文本（不含 *动作描述*）",
  "voice_key": "sandro_v1",
  "style": "",                                        // 可选，基底风格描述
  "defaults": {"cfg_value": 2.0, "inference_timesteps": 10, "seed": null},
  "format": "wav"
}
```

- 成功：`200`，正文是裸 `audio/wav` 字节。
- 声音不存在：`404 {"error": "voice_unknown", "voice_key": "..."}`。
- 服务在跑但模型没就绪（冷启动中）：`503 {"error": "model_not_ready", "state": "loading"}`。
- 简单到无聊是故意的：越薄的契约越换得动后端。

**`GET /health`**

```json
{"status": "ok", "engine": "voxcpm2", "model_state": "cold", "voice_count": 1}
```

`model_state` ∈ `cold | loading | ready`。ExoCore 用它区分"服务没开"和"在加载模型"——这两种情况对用户是不同的话（"语音服务暂时不可用" vs "语音服务正在启动"）。

## 声音资产

```text
voices/sandro_v1/
├── reference.wav    冻结下来的参考音频（选角的产物，要保住）
└── voice.json       display_name / engine / baseline_instruction / prompt_text /
                     generation_defaults / source（哪一批的第几号候选、seed、描述）
```

`voice.json` 与 ExoCore 的 `VoiceProfile` 一一对应：`name ← key`、`engine`、`baseline_instruction`、`generation_defaults`。`prompt_text` 是被朗读的那句原文，做 ultimate cloning（参考音频 + 逐字稿）时要用。

## 里程碑

| | 内容 | 状态 |
|---|---|---|
| **M1** | 仓库骨架 + 声音资产库 + 选角台（本文件描述的工具） | ✅ 已落地 |
| **M2** | `POST /tts` + `GET /health` 守护进程（`backends/fake` 用于契约测试 + `backends/voxcpm2` 真推理） | 待施工 |
| **M3** | ExoCore 适配器改 key-based + `base_url` 配置化（跨仓计划落 `ExoCore/Plan/`） | 待施工 |
| **M4** | 工具侧 `send_voice_msg` / 前端接线 | 待施工 |

M2 必须先裁决两件事：**模型常驻还是空闲卸载**（常驻吃 ~5.5GB 显存，跟游戏抢；卸载则首点要等几十秒），以及 **ExoCore 侧的超时口径**（现有适配器 10 秒超时 + 60 秒假死阈值，对冷启动 + 长文本不够用）。

## 环境

推理栈只装在它自己的环境里：

```bash
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m pip install -e .
E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests -v
```

（安装会在环境的 `Scripts/` 里生成一个 `exocore-cast`，`conda activate voxcpm_runtime` 后可直接当命令用；本文件统一用 `tools/cast.py` 举例，两者等价。）

- 环境：`voxcpm_runtime`（Python 3.10 + torch 2.5.1+cu121 + voxcpm 2.0.3）
- 权重：`~/.cache/huggingface/hub/models--openbmb--VoxCPM2`（4.7GB，已下载）
- 硬件实测（RTX 3060 Ti，可用显存约 6.8GB）：短/中/长三档全部通过，RTF ≈ 2.1，长段落峰值 ~6.4GB

## 选角（现在就能做的事）

```bash
# 1. 造一批候选：4 个描述 × 8 条台词 × 2 次抽取 = 64 条
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py design \
    --designs tools/sandro_designs.txt \
    --lines   tools/sandro_lines.txt \
    --out     candidates/round1 \
    --repeats 2

# 2. 听（盲目听！文件名只有编号，描述在 manifest.json 里，避免先入为主）
#    列出这批候选的情况：
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py list --from candidates/round1

# 3. 挑中的冻结成正式声线
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py pick \
    --from candidates/round1 --id 7 --key sandro_v1 --display-name Sandro

# 想用现成音频微调对比（克隆模式）
E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py clone \
    --reference path/to/take.wav --prompt-text "那句原文" \
    --lines tools/sandro_lines.txt --out candidates/round2
```

为什么这么设计：

- **Voice Design 不稳定**——同一个描述每次结果都不一样，所以一轮要多抽几次，命中一个满意的就**冻结**成参考音频，之后全部走克隆保持音色稳定。
- **盲听**：编号 + manifest 分离，挑的时候用的是耳朵。
- **可复现**：每条候选都记了 cfg / timesteps / seed / 实际送入模型的文本。
- **可续跑**：manifest 每完成一条就落盘，中断后重跑同一条命令会跳过已完成的。
- **省时间**：模型一批只加载一次；`--dry-run` 可以先看计划不花 GPU。

单条台词的时长大致是 `字数 × 0.35 秒`（RTF ≈ 2.1），一次选角建议 8~12 条台词，别一次跑几百条。

## 与其它仓库的关系

- 本仓是独立仓库（`git@github.com:XinyiS7/ExoCore-TTS.git`），本地挂在 `ExoCore_Project/ExoCore-TTS/`。
- 与 `ExoCore-Runtime` 同属"主仓旁边的服务仓"这一类：各自独立启停、独立环境、loopback HTTP 契约。
- 跨仓改动（本仓 + Django）的计划统一落 `ExoCore/Plan/`，不在这里另起一份。
