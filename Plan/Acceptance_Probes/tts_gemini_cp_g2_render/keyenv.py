"""Print the project key for the managed cloud voice, for shell injection.

Usage:  export GEMINI_API_KEY="$(python Plan/Acceptance_Probes/tts_gemini_cp_g2_render/keyenv.py)"

Why this exists: shell `sed`/quoting mangled the extraction once and the daemon silently fell
back to the *other* project's key (every request then 404s with "voice not found"). Parsing in
Python and *verifying the exported length before the run* removes that failure mode. The file
itself carries no key; the value only ever travels through the environment.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # keyenv.py -> dir -> Acceptance_Probes -> Plan -> repo


def read_key(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        head, sep, tail = line.partition("=")
        if sep and head.strip() == name:
            return tail.strip().strip('"').strip("'").strip()
    return ""


key = read_key(REPO / ".env", "GEM_TTS_KEY")
if not key:
    print("keyenv: GEM_TTS_KEY not found in ExoCore-TTS/.env", file=sys.stderr)
    sys.exit(1)
print(key, end="")
