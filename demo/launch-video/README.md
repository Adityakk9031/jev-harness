# jev-harness launch video

A ~3-minute motion-graphics launch video built with [Remotion](https://www.remotion.dev) (React → MP4)
from the real screenshots in `demo/shots/`. It covers the problem, the architecture and the four tools,
with narration, word-timed captions, music and sound effects.

Every number on screen comes from the real runs captured in `demo/shots/` (see `demo/shots/SHOTS.md`).
The compaction scene is a simulated run, and it is labelled that way on screen and in the voice-over.

## Pipeline

| Step | Command (from this folder) | Output |
|---|---|---|
| 1. Narration (Kokoro TTS, local) | `<tts-venv>/bin/python scripts/gen_voice.py kokoro-v1.0.onnx voices-v1.0.bin af_heart [line-id ...]` | `voice-raw/*.wav`, `src/narration-timing.json` |
| 2. Word timings (faster-whisper `small.en`, local) | `<tts-venv>/bin/python -I scripts/align.py` | `src/words.json` |
| 3. Voice clean-up, music, sound effects (numpy/scipy, synthesized) | `uv run --project ../.. python scripts/gen_audio.py` | `public/voice/*.wav`, `public/audio/*.wav` |
| 4. Preview stills | `node scripts/stills.mjs <dir> <frame> ...` | JPEGs at half size |
| 5. Render | `npx remotion render src/index.ts Launch out/launch-raw.mp4 --codec=h264 --crf=16 --color-space=bt709 --audio-codec=aac --audio-bitrate=320k` | `out/launch-raw.mp4` |
| 6. Loudness (two-pass `loudnorm`, -14 LUFS) | see the ffmpeg commands in the last section | `out/jev-harness-launch.mp4` |

Copy the screenshots into `public/shots/` before rendering (`cp ../shots/*.png public/shots/`).
`npm install` installs Remotion; the first render downloads Chrome Headless Shell.

The narration text lives in `narration.json` (copied to `src/narration.json`). Scene order, the pause
before each line (`lead`) and the hold after it (`tail`) live in `src/scene-config.json`. Both the
video (`src/timeline.ts`) and the music (`scripts/gen_audio.py`) read the same files, so the music's
drop, breaks and ducking follow the picture.

## How the picture is built

* `src/timeline.ts`: scene start frames, plus `at(scene, phrase)`, the frame a phrase is spoken
  (from `words.json`). Every camera move, callout and sound effect is keyed to the words, not to
  hand-typed frame numbers.
* `components/Shot.tsx`: a screenshot under a camera. It opens on the window floating in 3D, then
  flies to rectangles in `src/focus.json` (screenshot pixels) with a heavy ease and a glowing spotlight.
* `components/Camera.tsx`: the same camera for diagrams (used by the architecture scene).
* `components/Captions.tsx`: word-by-word captions; spoken numbers show as figures (`331`, `99%`).
* `scenes/`: Hook, Problem, Intro (logo), Architecture, the four tools (`Tools.tsx`), Promises, CTA.

## Loudness pass

```bash
ffmpeg -i out/launch-raw.mp4 -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null -
# then feed the measured_* values back in:
ffmpeg -i out/launch-raw.mp4 -c:v copy -af loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=..:measured_TP=..:measured_LRA=..:measured_thresh=..:offset=..:linear=true -ar 48000 -c:a aac -b:a 320k out/jev-harness-launch.mp4
```

## Licences

Music and sound effects are synthesized by `scripts/gen_audio.py`; no samples are used. The voice is
Kokoro-82M (Apache-2.0). Fonts are Inter and JetBrains Mono (SIL OFL) via `@remotion/google-fonts`.
Remotion is free for individuals and small companies; check its licence before using it at a larger company.
