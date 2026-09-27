# 0003 — M2 Independent Acceptance Verdict

> **Verdict:** **PASS** — CP-A, CP-B, smoke closure, and Amendment 01 accepted.
> **Acceptance owner:** Solaire / gpt-5.6-sol
> **Date:** 2026-09-27
> **Accepted implementation:** `668b463`, `a82f83a`, `fab2d9c`, `b8930c3`
> **Evidence:** checkpoint A/B packets, delivery result `0a7f19c`, smoke closure `a345ef0`, Amendment 01 plan `8af993b`, and Amendment repair evidence.

## Accepted contract and implementation

- `POST /tts` and `GET /health` match the frozen M2 wire contract, including exact error bodies, auth behavior, cold/loading/ready states, and single-worker execution.
- Lazy model load, configurable idle unload, load-failure recovery, serialization of inference, and shutdown behavior are implemented and exercised.
- Voice assets are key-based; no Django display-name fallback crosses the daemon boundary.
- `delivery` remains the only optional wire intent. The measured product probe did not pass Alicia's listening gate, so VoxCPM2 correctly advertises no support and returns `422 delivery_unsupported` rather than silently ignoring it.
- Real-machine smoke evidence covers cold load, concurrent cold requests, multi-segment assembly, EN/DE voices, idle eviction, injected load failure and same-process recovery.
- Alicia listened to the retained EN/DE short samples and judged them “没问题，现在不用纠结”. This is user listening evidence for those samples, not a universal quality guarantee.

## Amendment 01 focused acceptance

Amendment 01 is accepted with the approved scope:

- `SEGMENT_UNIT_BUDGET=120`;
- CJK ranges frozen by the amendment cost 3 units; other code points cost 1;
- sentence-final preference remains;
- an over-budget piece falls back through whitespace, clause punctuation, comma-level punctuation, then a hard unit-boundary cut;
- output remains ordered, non-empty, lossless apart from allowed seam whitespace, and within both the weighted budget and historical character outer bound;
- `casting.py` is outside the amendment;
- no runtime 12-second threshold exists.

Independent verification:

- full non-GPU suite: **159/159 PASS**, zero skip;
- focused text suite: **21/21 PASS**;
- independent boundary probes passed for 40/41 CJK, 120/121 Latin, mixed 120/121 units, punctuation fallback, no-empty, and normalized reassembly;
- source review found no realistic-input correctness or safety regression.

The repair lowers but does not eliminate VoxCPM2's long-segment level drift. Alicia explicitly accepts this residual engine limitation for the present product scope. Deliberately repetitive/adversarial text is not an acceptance invariant and does not trigger further segmentation work.

## Non-blocking follow-ups

These do not reopen M2:

1. Add a dedicated regression for punctuation exactly on the weighted boundary.
2. Restore the old explicit assertion that whitespace fallback does not split ordinary words, although current behavior was independently confirmed.
3. If factory assets ever become manually editable outside the managed tools, add explicit manifest-key/directory-key mismatch rejection.
4. Keep worker-thread ownership assumptions documented for direct runtime callers.

## Final disposition

M2 is accepted for delivery. Gate-0 remains a contract/document-consistency verdict; this file is the independent implementation verdict. ExoCore adapter and `send_voice_msg` work remain separate M3/M4 checkpoints and are not implied by this PASS.
