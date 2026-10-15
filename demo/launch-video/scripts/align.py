"""Word-level timing for the narration: transcribe each voice clip with faster-whisper, then align
the known narration words to the transcript so every word gets a start and end time.

Usage: python scripts/align.py   (from demo/launch-video, with faster-whisper installed)
Writes src/words.json: {scene_id: [{"w": word, "s": start_sec, "e": end_sec}, ...]}.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from faster_whisper import WhisperModel

ROOT = Path(__file__).resolve().parent.parent
ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def spell(n: int) -> list[str]:
    if n < 20:
        return [ONES[n]]
    if n < 100:
        return [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
    rest = n % 100
    return [ONES[n // 100], "hundred"] + (["and", *spell(rest)] if rest else [])


def norm(word: str) -> list[str]:
    """Lower-case tokens for matching; digits become words the way the narration spells them."""
    out: list[str] = []
    for part in re.split(r"[\s\-/]+", word.lower()):
        part = part.replace("base64", "base 64")
        for tok in re.findall(r"\d+%?|[a-z']+", part):
            if tok[0].isdigit():
                pct = tok.endswith("%")
                out += spell(int(tok.rstrip("%"))) + (["percent"] if pct else [])
            else:
                out.append(tok.strip("'"))
    return [t for t in out if t]


def main() -> None:
    narration = json.loads((ROOT / "narration.json").read_text())
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    result: dict[str, list[dict[str, object]]] = {}
    for item in narration:
        sid, text = item["id"], item["text"]
        segments, _ = model.transcribe(
            str(ROOT / "public" / "voice" / f"{sid}.wav"), word_timestamps=True, beam_size=5
        )
        heard: list[tuple[str, float, float]] = []  # (token, start, end)
        for seg in segments:
            for w in seg.words or []:
                toks = norm(w.word)
                step = (w.end - w.start) / max(1, len(toks))
                heard += [
                    (t, w.start + k * step, w.start + (k + 1) * step) for k, t in enumerate(toks)
                ]
        words = text.split()
        # Each narration word expands to one or more tokens; remember which word each came from.
        toks: list[str] = []
        owner: list[int] = []
        for i, w in enumerate(words):
            for t in norm(w):
                toks.append(t)
                owner.append(i)
        times: list[tuple[float, float] | None] = [None] * len(toks)
        sm = difflib.SequenceMatcher(None, toks, [h[0] for h in heard], autojunk=False)
        matched = 0
        for a, b, size in sm.get_matching_blocks():
            for k in range(size):
                times[a + k] = (heard[b + k][1], heard[b + k][2])
                matched += 1
        # Fill gaps by interpolating between the nearest matched neighbours.
        known = [i for i, t in enumerate(times) if t is not None]
        if not known:
            raise SystemExit(f"{sid}: nothing aligned")
        for i, t in enumerate(times):
            if t is not None:
                continue
            left = max((k for k in known if k < i), default=None)
            right = min((k for k in known if k > i), default=None)
            if left is None:
                s = times[right][0]  # type: ignore[index]
                times[i] = (s, s)
            elif right is None:
                e = times[left][1]  # type: ignore[index]
                times[i] = (e, e)
            else:
                le, rs = times[left][1], times[right][0]  # type: ignore[index]
                frac = (i - left) / (right - left)
                s = le + (rs - le) * frac
                times[i] = (s, s)
        out = []
        for i, w in enumerate(words):
            idx = [k for k, o in enumerate(owner) if o == i]
            if not idx:  # punctuation-only word
                prev = out[-1]["e"] if out else 0.0
                out.append({"w": w, "s": prev, "e": prev})
                continue
            out.append({"w": w, "s": round(times[idx[0]][0], 3), "e": round(times[idx[-1]][1], 3)})  # type: ignore[index]
        result[sid] = out
        print(
            f"{sid:9s} tokens={len(toks):3d} matched={matched:3d}  "
            f"heard: {' '.join(h[0] for h in heard)[:110]}"
        )
    (ROOT / "src" / "words.json").write_text(json.dumps(result, indent=1))
    print("wrote src/words.json")


if __name__ == "__main__":
    main()
