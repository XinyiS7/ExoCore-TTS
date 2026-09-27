# 0003 TTS Daemon — Review / Rewrite Handoff

> Date: 2026-09-27
> Reviewer: gpt-5.6-sol / Solaire
> Target: `Plan/0003_tts_daemon.md`
> Status: **REFERENCE-ONLY / REWRITE CONTRACT SECTIONS BEFORE CONSTRUCTION**

## 0. Disposition

`0003_tts_daemon.md` is useful as an implementation reference, but should **not** be treated as the final construction authority in its current form.

The good parts should be retained:

- independent TTS daemon/process;
- voice assets owned by ExoCore-TTS;
- lazy model load + idle unload;
- single-GPU serialized inference;
- fake backend for non-GPU contract tests;
- sentence segmentation/concat on the TTS side;
- raw `audio/wav` response;
- local/cloud backend dispatch owned entirely by this repository;
- real-machine smoke tests for zh/en/de voices.

The contract surface and lifecycle semantics need a substantial rewrite before implementation.

The central boundary is:

```text
ExoCore owns                    ExoCore-TTS owns
----------------------------   -------------------------------------
which voice_key is selected    engine/provider choice
text to speak                  local vs cloud routing
optional per-utterance         reference audio / prompt transcript
performance intent             baseline voice recipe
                               generation parameters
                               seed strategy
                               verification / retry policy
                               model/GPU lifecycle
                               segmentation / audio assembly
```

A backend swap must not require ExoCore production-code changes.

---

## 1. Two product paths must be kept distinct

### A. Generic user-triggered "read this message"

This path needs **no generation parameters from the user or ExoCore**.

Application semantics:

```text
user clicks TTS on an assistant message
    -> ExoCore computes canonical spoken projection
    -> POST /tts(text, voice_key)
    -> async render job
    -> button becomes ready
    -> user may play
```

No `emotion`, `pace`, `seed`, `cfg`, `timesteps`, `verify`, or style override is needed for this path.

The selected voice asset already defines the baseline delivery.

### B. Agent-authored `send_voice_msg`

This is a separate product feature. The model may intentionally choose a per-utterance performance on top of the selected base voice.

Recommended tool contract:

```text
send_voice_msg(
    content: string,
    delivery: string = ""
)
```

`delivery` is deliberately free-form natural language and engine-neutral. It may describe, for example:

- emotion / affect;
- tempo / rhythm;
- intensity / softness;
- pause character;
- restrained vs animated delivery.

Example:

```text
delivery = "intimate and very quiet; slower pace; restrained amusement; soften the final sentence"
```

This corresponds to the useful idea in provider UIs such as choosing a base speaker and then giving each utterance an additional delivery/expression direction.

Do **not** expose provider/backend knobs such as `cfg_value`, `inference_timesteps`, provider voice IDs, model IDs, or raw vendor tags in the tool schema.

If future non-verbal cues such as laugh/cough are wanted, add an engine-neutral cue contract only after both local/cloud adapters have a deliberate mapping. Do not make Gemini-specific `<laugh>`-style syntax part of the first public contract.

---

## 2. Public `/tts` contract should be narrow

The current draft exposes too much:

```text
style
seed
defaults.cfg_value
defaults.inference_timesteps
verify
```

Those are TTS-factory implementation details and should not be caller-controlled in the production application API.

Recommended request shape:

```json
{
  "text": "...",
  "voice_key": "sandro_v1",
  "delivery": "optional natural-language per-utterance direction"
}
```

Rules:

- Generic read-aloud omits `delivery`.
- `send_voice_msg` may set `delivery`.
- `delivery` augments the voice baseline; it does not replace the canonical voice recipe.
- Backend-specific parameter translation happens inside ExoCore-TTS.
- Quality verification/retry is a daemon/voice policy, not a caller flag.
- Seed strategy is internal.

If experimental parameter overrides are still desirable for casting/debugging, keep them in CLI/admin/debug surfaces, not production `/tts`.

---

## 3. Current VoxCPM2 parameter reality

The current local synthesis path in `casting.py` calls the model approximately with:

```text
text
cfg_value
inference_timesteps
reference_wav_path
prompt_wav_path
prompt_text
```

There is **no dedicated first-class `emotion` or `pace` argument in the current wrapper**.

Current casting design mode expresses a design by composing it into text (`(<design>)<line>`), while clone mode primarily relies on reference audio + transcript.

Therefore M2 should not invent an ExoCore-facing local-engine parameter schema. Instead, the VoxCPM2 backend should own a small capability/probe step for mapping `delivery` into its best supported prompting mechanism.

Required probe before freezing the Vox adapter:

1. clone/reference synthesis with no dynamic delivery;
2. same text/reference with a natural-language delivery instruction;
3. compare whether emotion/pace changes while voice identity remains stable;
4. confirm that the instruction does not get spoken literally;
5. record the chosen translation strategy inside the Vox backend only.

If a specific local engine cannot faithfully implement some delivery instruction, that is a backend capability issue, not a reason to leak engine knobs into ExoCore.

---

## 4. Spoken-text projection for generic read-aloud

Product rule from Alicia:

> Markdown *italic* action/stage-direction content is not spoken by default.

Recommendation: **do not add a blocking "confirm what will be spoken" dialog before every TTS request.** It would make the normal one-click read-aloud path unnecessarily heavy.

Instead:

1. ExoCore computes one deterministic canonical `spoken_text` projection using Markdown structure, not regex guessing.
2. Single-emphasis italic spans used by the chat convention are omitted.
3. Fenced/inline code and other explicitly non-spoken structures should be handled by the same projection policy.
4. The daemon receives already projected speech text and does not understand chat Markdown semantics.

Important limitation:

If the agent forgets to mark an action as italic, the system cannot reliably infer that the plain sentence was "really an action" without risking deletion of legitimate spoken dialogue. Do **not** add semantic guessing heuristics to compensate for authoring mistakes.

Recommended UX seam, optional but cheap:

- preserve/return the computed `spoken_text` in the ExoCore render state so the frontend can later offer a non-blocking "查看朗读文本" / preview affordance;
- do not require confirmation in the normal click path.

If authoring-format misses become frequent, fix the authoring/prompt/output convention rather than making TTS guess prose semantics.

---

## 5. README vs plan contract conflicts must be resolved before construction

Current documents disagree on at least:

- `unknown_voice` vs `voice_unknown`;
- cold start: `503 model_not_ready` vs one POST waiting through model load;
- health response shape;
- which fields are application contract vs diagnostic telemetry.

Only one authority may remain.

Recommended cold-start behavior:

```text
POST /tts
  cold -> loading -> render -> 200 audio/wav
```

The request may wait through loading because ExoCore invokes it from its own asynchronous render worker. `/health` remains independently responsive and reports loading.

Do not require ExoCore to poll/retry a synthetic `503 model_not_ready` loop just because the local model is warming.

---

## 6. Health/telemetry must not re-couple ExoCore to a backend

Backend diagnostics such as:

```text
engine name
CUDA device
infer seconds
RTF
segment count
```

are useful for humans and operations, but must **not** become M3 business-logic dependencies.

Separate:

### Stable application semantics

- synthesis success/failure;
- optional abstract service/render state;
- stable error codes.

### Diagnostic metadata

- `voxcpm2` / cloud provider identity;
- RTF;
- device;
- detailed timing;
- segment count.

ExoCore must not branch on backend names.

`GET /voices` similarly should treat `voice_key` as opaque. `engine` may be diagnostic, but caller code must not depend on it.

---

## 7. Concurrency / lifecycle invariants missing from the current draft

The rewritten plan must freeze these explicitly.

### 7.1 HTTP event loop remains responsive

Model loading and synthesis are blocking heavy work and must not block the FastAPI event loop.

While the first POST is loading the model, concurrent `GET /health` must still answer promptly with `loading`.

### 7.2 Exactly one load owner

Concurrent first requests must cause exactly one model load; later requests wait behind the same state/lock.

### 7.3 Load failure must be recoverable

A failed model load must never leave the daemon permanently stuck in `loading`.

Either:

```text
cold -> loading -> ready
               -> error -> retryable transition
```

or a precisely defined rollback to `cold`.

### 7.4 Idle-unload race

The unload worker and render path share ownership/locking. After acquiring the lock, the unload path must re-check idle eligibility before dropping the model so a fresh render cannot be followed by unload based on a stale pre-lock observation.

### 7.5 Single GPU queue honesty

Requests may queue. Do not fabricate percentage progress. Queue/load/render state may be reported only where actually known.

---

## 8. Request size and segmentation are different limits

Current `segment_max_chars` only controls per-segment length. It is not a total-request limit.

Add a distinct total guard, e.g.:

```text
EXOCORE_TTS_MAX_TEXT_CHARS
EXOCORE_TTS_SEGMENT_MAX_CHARS
```

The former prevents one request from monopolizing the GPU queue for an unbounded time; the latter controls inference chunk size.

---

## 9. Quality verification belongs inside the factory

The existing idea of local quality checks and bounded retries is fine, but caller-facing `verify: true/false` should be removed.

Possible ownership:

- daemon configuration;
- per-voice asset policy;
- debug CLI.

For long text, prefer segment-level validation/retry before final concatenation where practical, rather than regenerating the whole multi-segment utterance because one late segment failed.

---

## 10. Error boundary

Client-visible failures should be stable and small, e.g.:

```json
{"error": "unknown_voice"}
{"error": "invalid_request"}
{"error": "engine_unavailable"}
{"error": "synthesis_failed"}
```

Raw/scrubbed internal exception text should go to daemon logs, not the HTTP response. Even scrubbed exceptions may expose local paths, cache locations, device details, provider request fragments, etc.

ExoCore can collapse transport/backend failures further into its product-level message such as `TTS 服务暂时不可用`.

---

## 11. Network guard

Current loopback-default + optional bearer token is good.

Add one startup invariant:

```text
non-loopback bind AND no authentication token -> refuse startup
```

This prevents an accidental `0.0.0.0` development bind from exposing an unauthenticated synthesis service to the LAN.

---

## 12. Documentation cleanup required by the new boundary

Current `README.md` / `voices.py` still describe mirroring these TTS-owned fields into Django `VoiceProfile`:

```text
engine
baseline_instruction
generation_defaults
reference_audio_path
```

That is legacy authority and should be demoted.

New authority:

```text
ExoCore stores/selects: voice_key (+ UI display metadata if convenient)
ExoCore-TTS owns:      engine, references, baseline, defaults, provider config
```

Existing Django columns do not need an immediate destructive migration; first stop using them as source of truth.

Also, the current TTS config's fallback to sibling `ExoCore/.env` is tolerable for present manual cloud tooling, but a production cloud backend should eventually consume TTS-process-owned environment/secret configuration instead of depending on sibling checkout layout.

---

## 13. Required tests to add to the M2 rewrite

Retain existing planned tests, and explicitly add:

```text
concurrent first POSTs
  -> exactly one model load

slow fake loader + active POST
  -> GET /health remains responsive and reports loading

model-load failure
  -> no permanent loading state; next request can retry

idle unload racing with fresh render
  -> no stale-decision premature unload

backend swap/fake backend
  -> same application-level /tts shape

read-aloud request without delivery
  -> baseline voice path only

request with delivery
  -> backend receives engine-neutral delivery intent, without ExoCore-specific knobs
```

Real-machine smoke still verifies zh/en/de voices, long-text segmentation, cold load, and idle release.

---

## 14. Final recommendation to the next plan author

Use `0003_tts_daemon.md` as a **fact bank and implementation sketch**, not as the final spec.

Preserve its implementation work on:

- fake backend;
- Vox backend extraction from casting;
- model state machine;
- segmentation;
- idle unload;
- quality hooks;
- smoke procedure.

Rewrite the following sections from first principles using this handoff as authority:

- `/tts` request contract;
- cold-start semantics;
- `/health` application vs diagnostic contract;
- error contract;
- concurrency/state invariants;
- generic read-aloud vs `send_voice_msg` separation;
- dynamic per-utterance delivery contract;
- ExoCore / ExoCore-TTS ownership boundary.

The desired end state is intentionally boring:

```text
Generic read-aloud:
    ExoCore -> /tts(text, voice_key)

Agent voice message:
    ExoCore -> /tts(content, voice_key, delivery?)

Everything else stays behind the TTS service boundary.
```
