"""Generate the narration with Kokoro TTS. Run with the TTS venv:
    <venv>/bin/python scripts/gen_voice.py <model.onnx> <voices.bin> [voice] [line-id ...]
Writes voice-raw/<id>.wav (scripts/gen_audio.py cleans them into public/voice/) and
src/narration-timing.json (seconds per line).
"""

import json
import sys
from pathlib import Path

import espeakng_loader

# espeak-ng silently ignores a data path longer than its fixed buffer and falls back to the path
# baked in at build time, so the data is copied to a short path first.
SHORT_DATA = Path.home() / ".cache" / "jev-video" / "espeak-ng-data"
import soundfile as sf  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402
from kokoro_onnx.config import EspeakConfig  # noqa: E402

model, voices = sys.argv[1], sys.argv[2]
voice = sys.argv[3] if len(sys.argv) > 3 else "af_heart"
only = set(sys.argv[4:])  # regenerate just these lines; keep the others' timing
here = Path(__file__).resolve().parent.parent
lines = json.loads((here / "narration.json").read_text())
tts = Kokoro(
    model,
    voices,
    espeak_config=EspeakConfig(
        lib_path=espeakng_loader.get_library_path(), data_path=str(SHORT_DATA)
    ),
)
timing_path = here / "src" / "narration-timing.json"
timing = json.loads(timing_path.read_text()) if only and timing_path.exists() else {}
for line in lines:
    if only and line["id"] not in only:
        continue
    samples, rate = tts.create(line["text"], voice=voice, speed=1.05, lang="en-us")
    out = here / "voice-raw" / f"{line['id']}.wav"
    sf.write(out, samples, rate)
    timing[line["id"]] = round(len(samples) / rate, 3)
    print(f"{line['id']:9} {timing[line['id']]:6.2f}s")
timing_path.write_text(json.dumps(timing, indent=2))
print("total", round(sum(timing.values()), 1), "s")
