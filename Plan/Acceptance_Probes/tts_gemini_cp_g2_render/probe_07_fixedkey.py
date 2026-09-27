"""Corrected live round: right key, free access gate, then two renders.

The earlier daemon rounds ran with an empty injected key, so the daemon silently used the
*other* project's key and every request 404'd. This script therefore:

1. proves the key in the daemon's environment can actually see the voice (free `voices.get`,
   no render, no cost) -- and refuses to continue otherwise;
2. renders zh with the asset baseline (matrix sample / A leg);
3. renders zh with a short caller-supplied style (the B-leg question: does a <=500-char style
   resolve the voice, i.e. is per-request delivery usable at all).

One request per step, first failure stops.
"""
import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import soundfile as sf

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[2]
sys.path.insert(0, str(REPO / "src"))

from exocore_tts import cloud, voices  # noqa: E402

ZH = "天文课结束。现在，闭上眼睛，立刻休眠。"
ALE = (REPO / "tools/cloud/prompts/ale.txt").read_text(encoding="utf-8").strip()
VOICE_KEY = "sandro_gemini_v1"


def short_style() -> str:
    """The caller-side B-leg style: 'Style: ' + a verbatim <=500-char fragment."""
    start = ALE.index("When addressing")
    chosen = ""
    for index, piece in enumerate(ALE[start:].split(". ")):
        candidate = piece if index == 0 else f"{chosen}. {piece}"
        if len("Style: " + candidate) > 500:
            break
        chosen = candidate
    return "Style: " + chosen


def post(text: str, delivery: str):
    payload = {"text": text, "voice_key": VOICE_KEY}
    if delivery:
        payload["delivery"] = delivery
    request = urllib.request.Request(
        "http://127.0.0.1:8769/tts",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return response.status, response.read()


def main() -> int:
    asset = voices.load_voice(VOICE_KEY)
    kind, value = voices.cloud_voice_ref(asset)
    key = cloud.read_api_key()  # env first: exactly what the daemon resolves
    print(f"daemon key length: {len(key)} | style baseline: {len(asset.baseline_style)} chars")
    try:
        seen = cloud.CloudVoiceClient(api_key=key).render  # noqa: F841 - shape only, no call
        from google import genai

        client = genai.Client(api_key=key)
        got = client.voices.get(value)
        print(f"free access gate: OK -> {got.model_dump().get('display_name')!r} ({kind})")
    except Exception as exc:
        print(f"gate FAIL: {type(exc).__name__}: {str(exc)[:120]}")
        return 2

    results = []
    for label, delivery in (("probe_07_zh_baseline", ""), ("probe_07_zh_short_style", short_style())):
        entry = {"label": label, "delivery_chars": len(delivery)}
        try:
            status, data = post(ZH, delivery)
        except urllib.error.HTTPError as exc:
            entry.update(status=exc.code, error=exc.read().decode("utf-8", "replace")[:120])
            results.append(entry)
            break
        info = sf.info(io.BytesIO(data))
        entry.update(
            status=status,
            bytes=len(data),
            seconds=round(info.frames / info.samplerate, 2),
            sha256=hashlib.sha256(data).hexdigest()[:16],
        )
        (OUT / f"{label}.wav").write_bytes(data)
        entry["wav"] = f"{label}.wav"
        results.append(entry)

    (OUT / "probe_07_results.json").write_text(
        json.dumps({"jobs": results}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for entry in results:
        print(json.dumps(entry, ensure_ascii=False)[:200])
    ok = len(results) == 2 and all(e.get("status") == 200 for e in results)
    print("BOTH OK" if ok else "STOPPED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
