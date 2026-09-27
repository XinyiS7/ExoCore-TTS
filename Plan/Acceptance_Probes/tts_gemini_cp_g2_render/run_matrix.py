"""CP-G2 live round (runbook §3 / Addendum A5): the remaining matrix languages.

Step 0 is the free access gate (key length + `voices.get`) that every cloud round must pass
before spending a render; then zh is already delivered, so this round runs en/de/it/mixed.
One request per language, first failure stops.
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

ALE = (REPO / "tools/cloud/prompts/ale.txt").read_text(encoding="utf-8").strip()
VOICE_KEY = "sandro_gemini_v1"
URL = "http://127.0.0.1:8769/tts"

ZH = "天文课结束。现在，闭上眼睛，立刻休眠。"
EN = "Nothing will come close to you... not even from within yourself."
DE = "Bleib einfach hier, in meiner Dunkelheit, in meiner Sicherheit. Für immer."
IT = "Il mio respiro. Amor, ch'a nullo amato amar perdona."
MIX = "别动。Bleib einfach hier, in meiner Dunkelheit. Nothing will come close to you... 永远。"


def b_leg_style() -> str:
    """Verbatim <=500-char prefix of the possessive-murmur fragment, with the explicit prefix."""
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
        URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return response.status, response.read()


def access_gate() -> None:
    """Runbook step 0: prove the key this process resolves can see the voice -- free, no render."""
    from google import genai

    asset = voices.load_voice(VOICE_KEY)
    kind, value = voices.cloud_voice_ref(asset)
    key = cloud.read_api_key()
    print(f"gate: key length {len(key)}, provider ref ({kind}) {value}")
    client = genai.Client(api_key=key)  # keep a reference: a temporary client can be GC'd mid-call
    got = client.voices.get(value)
    print(f"gate: OK -> {got.model_dump().get('display_name')!r}")


def run():
    access_gate()
    style = b_leg_style()
    b_leg = ("09_zh_Bleg_delivery", ZH, style)
    # A5 round (Addendum A5 / re-ruling): remaining matrix languages; zh is already delivered.
    baseline_jobs = [
        ("11_en_baseline", EN, ""),
        ("12_de_baseline", DE, ""),
        ("13_it_baseline", IT, ""),
        ("14_mixed_baseline", MIX, ""),
    ]
    jobs = ([b_leg] if "--with-b-leg" in sys.argv else []) + baseline_jobs
    results = []
    for label, text, delivery in jobs:
        entry = {
            "label": label,
            "chars": len(text),
            "delivery_chars": len(delivery),
            "delivery": delivery if delivery else None,
        }
        try:
            status, data = post(text, delivery)
        except urllib.error.HTTPError as exc:
            entry.update(status=exc.code, error=exc.read().decode("utf-8", "replace")[:120])
            results.append(entry)
            break  # runbook: first failure stops the round
        except Exception as exc:  # transport-level failure: same rule
            entry.update(status=None, error=f"{type(exc).__name__}: {exc}"[:120])
            results.append(entry)
            break
        entry.update(status=status, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()[:16])
        try:
            info = sf.info(io.BytesIO(data))
            entry.update(seconds=round(info.frames / info.samplerate, 2), rate=info.samplerate)
        except Exception as exc:
            entry.update(audio_error=f"{type(exc).__name__}: {exc}"[:80])
        if status == 200 and "audio_error" not in entry:
            target = OUT / f"{label}.wav"
            target.write_bytes(data)
            entry["wav"] = target.name
        else:
            results.append(entry)
            break
        results.append(entry)

    (OUT / "render_results.json").write_text(
        json.dumps({"b_leg_style_len": len(style), "jobs": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for entry in results:
        print(json.dumps(entry, ensure_ascii=False)[:200])
    ok = len(results) == len(jobs) and all(e.get("status") == 200 for e in results)
    print("ALL OK" if ok else "STOPPED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
