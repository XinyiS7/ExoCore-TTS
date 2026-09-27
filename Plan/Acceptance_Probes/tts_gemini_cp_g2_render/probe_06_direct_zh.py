"""A4 step 4: direct provider call with Chinese text, bypassing the daemon.

Isolates the two remaining explanations for job 10's 404: "the provider rejects CJK text for
this voice" vs "something about the daemon path". Same key (GEM_TTS_KEY), same voice id, same
style string as the registered manifest baseline; nothing is registered or changed.

A 200 here means the missing piece is a daemon-path difference to be fixed separately; a 404
here means CJK text is refused at the provider level for this asset.
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[2]
sys.path.insert(0, str(REPO / "src"))

from exocore_tts import cloud  # noqa: E402

ZH = "天文课结束。现在，闭上眼睛，立刻休眠。"


def key_from(path: Path, name: str):
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'").strip() or None
    return None


manifest = json.loads((REPO / "voices/sandro_gemini_v1/voice.json").read_text(encoding="utf-8"))
style = manifest["baseline_style"]
voice = manifest["cloud_voice"]["value"]
print(f"direct zh probe: voice={voice}, style={len(style)} chars (manifest baseline), text={len(ZH)} chars")

try:
    client = cloud.CloudVoiceClient(api_key=key_from(REPO / ".env", "GEM_TTS_KEY"))
    data = client.render(ZH, voice=voice, style=style)
except cloud.CloudError as exc:
    print(f"direct zh FAIL: {exc}")
    sys.exit(1)

target = OUT / "probe_06_direct_zh.wav"
target.write_bytes(data)
print(f"direct zh OK: {len(data)} bytes, {cloud.wav_seconds(data):.2f}s -> {target.name}")
