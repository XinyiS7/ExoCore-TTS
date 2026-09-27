# 0003 — M2 收口 follow-ups 行为备忘（4 条 non-blocking）

> **状态**：施工前行为备忘（局部工作，代替详细计划）。**来源**：独立裁决 `f9be708` / `Plan/0003_m2_independent_acceptance.md` 的「Non-blocking follow-ups」（该裁决明确 *does not reopen M2*）。**指令**：Alicia（2026-09-27）——做完这 4 条，然后推送。

## 目标

把裁决记录里的 4 条可选加固做完，让 M2 的账在测试与注释层面同样闭合；**不改任何运行期行为**。

## 逐条范围与做法

1. **加权预算边界上的标点回归**（测试，新增）
   `test_punctuation_exactly_on_the_weighted_boundary`：标点恰落在预算最后一个可用单位上时（从句标点 `117 + 3 = 120`、拉丁逗号 `119 + 1 = 120`，另加一组显式小预算 `33` 的同形用例），切点必须**落在标点之后**、首段恰为预算值、标点不得被推到下一段；不出现硬切或丢字。
2. **恢复「空白回退不拆普通词」断言**（测试，修复）
   `test_over_long_sentence_prefers_whitespace_over_hard_cuts` 恢复修订时被误删的断言：`set(segment.split()) == {"word"}`（没有普通词被劈开）与重装一致断言。行为本身已被独立复核确认，这里只补回断言。
3. **manifest key / 目录名不一致的显式拒绝 —— 只记录触发条件**（文档）
   裁决原文为条件式（"If factory assets ever become manually editable outside the managed tools, add explicit manifest-key/directory-key mismatch rejection"）。当前资产只由受管工具链 `tools/cast.py -> freeze_voice` 写入，key 与目录名必然一致，**故不新增代码**；把该触发条件与届时要做的动作写进 `src/exocore_tts/voices.py` 模块 docstring（在"manifest 是唯一权威"一节之后），留给未来直接编辑资产的人。
4. **worker 线程归属假设的文档**（文档）
   在 `src/exocore_tts/runtime.py` 模块 docstring 增加 *Thread ownership* 段，面向**直接调用方**（service / tooling / tests）：加载与推理只在单一 worker 线程上；`run` 是接触模型的唯一入口，worker 内再入 `run` 会 `RuntimeError` 而非死锁；驱逐在同一把锁下复核，重释放发生在空闲监视 job 或直接调用 `evict_if_idle`/`close` 的线程上，且只在该模型已无人引用之后；`state` 为纯锁快照（任意线程安全），`close` 幂等并排空 worker。

## 不做

- 不引入 F-02 / 句级合并（Alicia 已裁定对抗性重复文本不在验收不变式内）；
- 不改 `text.py` 行为、`service.py`、wire/生命周期、`casting.py`；
- 不跑 GPU、不生成新音频产物。

## 验证

- `E:/Miniconda3/envs/voxcpm_runtime/python.exe -m unittest discover -s tests`：全量非 GPU，预期 **160 = 159 + 1**（新增 1 个用例；其余为既有用例内的断言恢复）；
- 改动文件的行尾/空白基线检查（LF、无尾随空白）。

## 附带（同一收口动作，Alicia 指示）

- `Plan/0003_review_handoff_solaire.md` 归档到 `Plan/Archived/`（沿用兄弟仓既有的 `Plan/Archived/` 约定）；
- 同步 `Plan/0003_tts_daemon.md` 第 7 行对该文件的引用路径（归档后仍可定位）。
