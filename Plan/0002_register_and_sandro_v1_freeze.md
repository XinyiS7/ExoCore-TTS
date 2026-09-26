# 0002 — register 子命令 与 sandro_v1 冻结

**目标**：把「外部产出的参考音频」（云端渲染、手工剪的片段）纳入选角台，并冻结第一条正式声线。

**背景**：M1 的 `pick` 只能从本批候选池里挑。Alicia 最终选中的参考来自 Gemini 云端（本地抽卡都不满意），选角台原本收不进来。

**行为变化**

- `voices.describe_clip(path)`：参考音频体检——可读性、时长 ≥ 3s、峰值 ≥ 0.02；削顶只警告不拒绝。
- `casting.register_reference(clip, key, transcript=…, display_name=…, style=…, engine=…, origin=…, force=…)`：逐字稿强制（reference-only 是本模型最弱的克隆模式，终级克隆必须有逐字稿）。
- CLI 新增 `cast.py register --clip … --key … --transcript[-file] … [--force]`；`cast.py list --voices` 增列参考片段时长。

**范围**：只碰 `src/exocore_tts/{voices,casting}.py` 与 `tests/test_casting.py`。M1 既有行为不动——`save_voice` 仍是「拷贝 + 原子写 manifest」，体检只在新路径上做。

**验证**：`python.exe -m unittest discover -s tests` → 33 passed（新增 6 个：冻结与溯源字段、逐字稿强制、静音/过短拒收、削顶警告、无 `--force` 不覆盖）。

**冻结产物**：`voices/sandro_v1/reference.wav`（27.20s / 24kHz / peak 0.906）+ `voice.json`（来源、逐字稿、风格指令、生成参数）。

**本次探测留下的经验（供 M2 与 README 引用）**

- **括号风格前缀在克隆模式下无效**：模型会把指令当正文念出来（`（加重并略微放慢…）` 被朗读）。该机制只在 Voice Design 模式有效。
- **多音字错读是随机的**：`还` 出现一次 `huán`，随后三次重跑全对 → 靠重试规避，不改写文本。
- **自动发音抽检只能当报警器**：单次 ASR 会因语调误报（`cand_0005` 被听成缺字，人耳判定完全正确）。
- **参考音频同时承载音色、口音与语速**（同句产出时长贴着参考时长），所以参考必须按目标语言分别准备。
