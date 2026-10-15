"""All the sound for the launch video, synthesized here: no samples, nothing licensed.

Reads the scene timeline (src/scene-config.json, src/narration-timing.json, src/words.json) so the
music follows the picture: tension under the hook, a drop on the logo, a breath at every tool
title, a build into the ending, and ducking under every spoken line. Also cleans the narration
(voice-raw/ -> public/voice/) and writes the sound effects to public/audio/.

    uv run --project ../.. python scripts/gen_audio.py      (needs numpy + scipy)
"""

from __future__ import annotations

import json
import math
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, istft, resample_poly, sosfilt, stft

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "public" / "audio"
SR = 48000
rng = np.random.default_rng(11)


# ---------------------------------------------------------------- helpers
def write(path: Path, x: np.ndarray) -> None:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        sr, ch, n, sw = w.getframerate(), w.getnchannels(), w.getnframes(), w.getsampwidth()
        raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
    else:  # soundfile writes float/int32 for some inputs
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2**31
    return x.reshape(-1, ch).mean(1), sr


def filt(kind: str, x: np.ndarray, hz, order: int = 2) -> np.ndarray:
    return sosfilt(butter(order, hz, kind, fs=SR, output="sos"), x, axis=0)


def lp(x, hz, order=2):
    return filt("low", x, hz, order)


def hp(x, hz, order=2):
    return filt("high", x, hz, order)


def bp(x, lo, hi, order=2):
    return filt("band", x, [lo, hi], order)


def db(v: float) -> float:
    return 10 ** (v / 20)


def peak_norm(x: np.ndarray, peak_db: float = -1.0) -> np.ndarray:
    return x / (np.max(np.abs(x)) or 1.0) * db(peak_db)


def rms_db(x: np.ndarray) -> float:
    return 20 * math.log10(float(np.sqrt(np.mean(x**2))) + 1e-12)


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def tt(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def saw(f, t):
    return 2.0 * ((t * f) % 1.0) - 1.0


def stereo(x: np.ndarray) -> np.ndarray:
    return x if x.ndim == 2 else np.stack([x, x], 1)


def make_ir(seconds: float, bright: float, seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    t = tt(seconds)
    ir = r.standard_normal((len(t), 2)) * np.exp(-6.9 * t / seconds)[:, None]
    ir = lp(ir, bright)
    fade = int(0.008 * SR)
    ir[:fade] *= np.linspace(0, 1, fade)[:, None]
    return ir / np.sqrt(np.sum(ir**2, axis=0))


HALL = make_ir(2.4, 5500, 1)
ROOM = make_ir(0.5, 7000, 2)


def reverb(x: np.ndarray, ir: np.ndarray, wet: float) -> np.ndarray:
    x = stereo(x)
    y = np.stack([fftconvolve(x[:, c], ir[:, c])[: len(x)] for c in range(2)], 1)
    return x * (1 - wet) + y * wet


def place(buf: np.ndarray, at: float, x: np.ndarray, gain: float = 1.0) -> None:
    i = round(at * SR)
    x = stereo(x)
    if i >= len(buf) or i + len(x) <= 0:
        return
    a, b = max(0, i), min(len(buf), i + len(x))
    buf[a:b] += gain * x[a - i : b - i]


def sweep_noise(seconds: float, center, width_oct: float, seed: int = 0) -> np.ndarray:
    """Noise through a band-pass whose centre follows `center(t)` (STFT filter, so it can move)."""
    r = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = []
    for _ in range(2):
        f, frames, spec = stft(r.standard_normal(n), fs=SR, nperseg=1024)
        c = center(frames)[None, :]
        mask = np.exp(-0.5 * (np.log2(np.maximum(f[:, None], 1) / c) / width_oct) ** 2)
        _, y = istft(spec * mask, fs=SR, nperseg=1024)
        out.append(y[:n])
    return np.stack(out, 1)


def compress(x: np.ndarray, thr_db: float, ratio: float, att: float, rel: float) -> np.ndarray:
    level = np.abs(x)
    env = np.empty_like(level)
    a, r = math.exp(-1 / (att * SR)), math.exp(-1 / (rel * SR))
    e = 0.0
    for i, v in enumerate(level):  # one-pole peak follower (voice clips are short)
        e = a * e + (1 - a) * v if v > e else r * e + (1 - r) * v
        env[i] = e
    over = np.maximum(env, 1e-9) / db(thr_db)
    gain = np.where(over > 1, over ** (1 / ratio - 1), 1.0)
    return x * gain


# ------------------------------------------------------ timeline (mirrors src/timeline.ts)
def js_round(v: float) -> int:
    return math.floor(v + 0.5)


cfg = json.loads((ROOT / "src" / "scene-config.json").read_text())
timing = json.loads((ROOT / "src" / "narration-timing.json").read_text())
words = json.loads((ROOT / "src" / "words.json").read_text())
FPS = cfg["fps"]
SC: dict[str, dict[str, float]] = {}
frame = 0
for sid in cfg["order"]:
    lead = js_round(cfg["lead"].get(sid, cfg["defaultLead"]) * FPS)
    voice = js_round(timing[sid] * FPS)
    tail = js_round(cfg["tail"].get(sid, cfg["defaultTail"]) * FPS)
    SC[sid] = {"start": frame / FPS, "lead": lead / FPS, "dur": (lead + voice + tail) / FPS}
    frame += lead + voice + tail
TOTAL = frame / FPS
print(f"timeline: {TOTAL:.2f}s, drop at {SC['intro']['start']:.2f}s")


# ---------------------------------------------------------------- narration clean-up
def clean_voice() -> None:
    out_dir = ROOT / "public" / "voice"
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted((ROOT / "voice-raw").glob("*.wav")):
        x, sr = read_mono(path)
        g = math.gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g)
        x = hp(x, 75, 2)
        x = x + 0.30 * bp(x, 2500, 6000, 2) + 0.12 * lp(x, 220, 2)  # presence and a little warmth
        x = compress(x, -26, 3.0, 0.004, 0.09)
        act = np.abs(x) > 0.004
        x = x * db(-18.5 - rms_db(x[act]))
        x = reverb(x, ROOM, 0.07)[:, 0]
        x = np.tanh(x / db(-2.0)) * db(-2.0)  # soft ceiling at -2 dBFS
        write(out_dir / path.name, x)
    print("voice: cleaned", len(list(out_dir.glob("*.wav"))), "clips")


# ---------------------------------------------------------------- instruments
def kick() -> np.ndarray:
    t = tt(0.5)
    f = 46 + 120 * np.exp(-t / 0.032)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.3)
    click = hp(rng.standard_normal(len(t)), 2500) * np.exp(-t / 0.004) * 0.3
    return np.tanh(1.8 * (body + click)) * 0.9


def clap() -> np.ndarray:
    t = tt(0.6)
    noise = bp(rng.standard_normal(len(t)), 900, 5000)
    env = np.zeros(len(t))
    for d in (0.0, 0.010, 0.021):
        m = t >= d
        env[m] += np.exp(-(t[m] - d) / 0.007)
    m = t >= 0.03
    env[m] += 0.8 * np.exp(-(t[m] - 0.03) / 0.11)
    return reverb(noise * env * 0.7, ROOM, 0.25)


def hat(decay: float) -> np.ndarray:
    t = tt(min(0.6, decay * 8))
    metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (2100, 3170, 4230, 5370, 6800, 8400))
    x = hp(0.25 * metal + rng.standard_normal(len(t)), 7500, 4)
    return x * np.exp(-t / decay) * 0.35


def snare() -> np.ndarray:
    t = tt(0.35)
    tone = np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.05)
    noise = bp(rng.standard_normal(len(t)), 1400, 9000) * np.exp(-t / 0.1)
    return 0.5 * tone + 0.8 * noise


def crash() -> np.ndarray:
    t = tt(3.0)
    x = hp(rng.standard_normal((len(t), 2)), 3200, 2) * np.exp(-t / 0.8)[:, None]
    return x * 0.35


def sub_note(midi: int, length: float) -> np.ndarray:
    t = tt(length)
    f = hz(midi)
    env = np.minimum(1, t / 0.006) * np.exp(-t / 0.22)
    body = lp(saw(f, t), 700, 2) * 0.45
    return (np.sin(2 * np.pi * f / 2 * t) * 0.9 + body) * env


def pad_chord(notes: list[int], length: float, cutoff: float) -> np.ndarray:
    t = tt(length + 1.2)
    out = np.zeros((len(t), 2))
    for c, spread in enumerate(((-0.11, -0.04, 0.03, 0.09), (-0.08, -0.02, 0.05, 0.12))):
        for m in notes:
            for k, d in enumerate(spread):
                out[:, c] += saw(hz(m + d), t + 0.37 * k / hz(m))
    env = np.minimum(1, t / 0.35) * np.where(t > length, np.exp(-(t - length) / 0.4), 1)
    return lp(out, cutoff, 2) * env[:, None] / (len(notes) * 4)


def pluck(midi: int) -> np.ndarray:
    t = tt(0.9)
    f = hz(midi)
    s = sum(saw(f * d, t) for d in (0.997, 1.0, 1.003)) / 3
    return (
        0.55 * lp(s, 5200, 2) * np.exp(-t / 0.05) + lp(s, 1500, 2) * np.exp(-t / 0.24)
    ) * np.minimum(1, t / 0.002)


# F#m - D - A - E, two bars each; voicings kept close for smooth movement.
PADS = [[54, 57, 61, 66], [54, 57, 62, 66], [52, 57, 61, 64], [52, 56, 59, 64]]
ROOTS = [42, 38, 45, 40]
ARP = [0, 1, 2, 3, 2, 1, 2, 3, 0, 1, 2, 3, 3, 2, 1, 2]


def music() -> np.ndarray:
    bpm = 120
    beat = 60 / bpm
    bar = 4 * beat
    drop = SC["intro"]["start"]
    end = TOTAL
    n = int((end + 0.5) * SR)
    drums = np.zeros((n, 2))
    bass = np.zeros((n, 2))
    pads = np.zeros((n, 2))
    arps = np.zeros((n, 2))
    fx = np.zeros((n, 2))

    kick_hit, clap_hit, hat_closed, hat_open, snare_hit, crash_hit = (
        kick(),
        clap(),
        hat(0.022),
        hat(0.14),
        snare(),
        crash(),
    )
    pluck_cache: dict[int, np.ndarray] = {}

    t_problem = SC["problem"]["start"]
    t_prom = SC["promises"]["start"]
    t_cta = SC["cta"]["start"]
    openers = [SC[s]["start"] for s in ("guard1", "cal1", "comp", "rev1")]
    breaks = []
    for t0 in openers:
        k = math.ceil((t0 + 1.6 - drop) / bar)
        breaks.append((t0, drop + k * bar))

    def in_break(t: float) -> bool:
        return any(a - 0.02 <= t < b - 0.02 for a, b in breaks)

    first = -math.floor(drop / beat)
    kicks: list[float] = []
    for k in range(first, int((end - drop) / beat) + 1):
        t = drop + k * beat
        if t < 0 or t >= end:
            continue
        step = k % 4
        barno = math.floor(k / 4)
        chord = (barno // 2) % 4
        if t < drop:
            # Tension: soft ticking, a filtered pulse from the problem scene on, then a roll
            if t >= SC["hook"]["lead"] + 0.5:
                place(drums, t + beat / 2, hat_closed, 0.25)
                if t >= drop - 4 * bar:
                    place(drums, t + beat / 4, hat_closed, 0.15)
                    place(drums, t + 3 * beat / 4, hat_closed, 0.15)
            if t >= t_problem:
                for e in range(2):
                    tb = t + e * beat / 2
                    if tb < drop - beat / 2:
                        cut = 250 + 1100 * (tb - t_problem) / max(1e-6, drop - t_problem)
                        place(bass, tb, lp(sub_note(42, beat / 2), cut), 0.55)
            if drop - bar <= t < drop:
                for s in range(4):
                    ts = t + s * beat / 4
                    if ts < drop - beat / 4:
                        place(drums, ts, snare_hit, 0.15 + 0.5 * (ts - (drop - bar)) / bar)
            continue
        if t >= t_cta:
            continue
        if in_break(t):
            continue
        building = t >= t_prom
        place(drums, t, kick_hit, 1.0)
        kicks.append(t)
        if step in (1, 3):
            place(drums, t, clap_hit, 0.55)
        place(drums, t + beat / 2, hat_open, 0.45)
        for s in (1, 3):
            place(drums, t + s * beat / 4, hat_closed, 0.3)
        for e in range(2):
            place(bass, t + e * beat / 2, sub_note(ROOTS[chord], beat / 2), 0.6)
        for s in range(4):
            idx = ARP[(step * 4 + s) % 16]
            m = PADS[chord][idx] + 12
            if m not in pluck_cache:
                pluck_cache[m] = pluck(m)
            place(arps, t + s * beat / 4, pluck_cache[m], 0.16 if not building else 0.2)
        if building:
            # Accelerating roll over the last bars into the ending.
            left = t_cta - t
            subdiv = 2 if left > 2 * bar else 4 if left > bar else 8
            for s in range(subdiv):
                ts = t + s * beat / subdiv
                if ts < t_cta - 0.06:
                    place(drums, ts, snare_hit, 0.12 + 0.45 * (1 - left / (t_cta - t_prom)))
    # Crashes on the drop, after each break, and into the ending.
    for t in [drop] + [b for _, b in breaks]:
        place(drums, t, crash_hit, 0.9)

    # Pads: every two bars, with the filter opening through the video.
    for barno in range(math.floor(-drop / bar) - 1, int((end - drop) / bar) + 1, 2):
        t = drop + barno * bar
        if t + 2 * bar < 0 or t >= t_cta:
            continue
        chord = (barno // 2) % 4
        if t < drop:
            chord = 0
            cut = 500 if t < t_problem else 900
            gain = 0.5 if t < t_problem else 0.7
        else:
            cut = 1600 if t < t_prom else 2600
            gain = 1.0
        length = min(2 * bar, t_cta - t) if t < t_cta else 2 * bar
        if t < drop:
            length = min(length, drop - t)
        place(pads, t, pad_chord(PADS[chord], length, cut), gain)

    # Drone under the tension section.
    t = tt(drop)
    drone = (np.sin(2 * np.pi * hz(30) * t) * 0.6 + lp(saw(hz(42), t), 160, 2) * 0.5) * np.minimum(
        1, t / 3.0
    )
    drone *= np.clip((drop - t) / 0.3, 0, 1)
    place(fx, 0, drone, 0.5)

    # Ending: one big chord, a sub, a crash, a long tail.
    tail = max(1.0, end - t_cta)
    place(pads, t_cta, pad_chord([45, 57, 61, 64, 69, 73], tail - 0.8, 3200), 1.4)
    tt_end = tt(tail)
    place(fx, t_cta, np.sin(2 * np.pi * hz(33) * tt_end) * np.exp(-tt_end / 1.6), 0.7)
    place(drums, t_cta, crash_hit, 1.0)

    # Sidechain pump from the kicks onto bass, pads and arps.
    pump = np.ones(n)
    for t0 in kicks:
        i = int(t0 * SR)
        seg = np.arange(int(0.45 * SR)) / SR
        j = min(n, i + len(seg))
        pump[i:j] = np.minimum(pump[i:j], 1 - 0.7 * np.exp(-seg[: j - i] / 0.1))
    pumped = (bass + pads * 0.8 + arps) * pump[:, None]
    # Stereo delay on the arps for width.
    d1, d2 = int(0.375 * SR), int(0.1875 * SR)
    delayed = np.zeros_like(arps)
    delayed[d1:, 0] += arps[:-d1, 0] * 0.35
    delayed[d2:, 1] += arps[:-d2, 1] * 0.35
    mix = drums * 0.9 + pumped + delayed * pump[:, None] + fx
    mix = mix + 0.18 * (reverb(pads + arps + 0.5 * drums, HALL, 1.0) - 0)  # send
    mix = hp(mix, 30, 2)
    # Level: the groove (no voice) sits at about -21 dBFS RMS; the narration is about -18.5.
    groove = mix[int(drop * SR) : int(t_prom * SR)]
    mix *= db(-21 - rms_db(groove))
    mix = np.tanh(mix / db(-3)) * db(-3)

    # Duck under every spoken line.
    voice = np.zeros(n)
    for sid, s in SC.items():
        w = words[sid]
        a = s["start"] + s["lead"] + w[0]["s"] - 0.1
        b = s["start"] + s["lead"] + w[-1]["e"] + 0.25
        voice[max(0, int(a * SR)) : min(n, int(b * SR))] = 1
    win = np.hanning(int(0.3 * SR))
    smooth = np.convolve(voice, win / win.sum(), mode="same")
    duck = 1 - (1 - db(-9)) * smooth
    mix *= duck[:, None]
    fade = np.clip((end + 0.5 - np.arange(n) / SR) / 2.5, 0, 1)
    return mix * fade[:, None]


# ---------------------------------------------------------------- effects
def whoosh() -> np.ndarray:
    d = 0.8
    x = sweep_noise(
        d, lambda t: 350 + 5200 * np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2, 0.6, seed=3
    )
    t = tt(d)[: len(x)]
    env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2.2
    pan = np.clip(t / d, 0, 1)
    x[:, 0] *= env * np.cos(pan * np.pi / 2) * 1.3
    x[:, 1] *= env * np.sin(pan * np.pi / 2) * 1.3
    return reverb(x, ROOM, 0.2)


def impact() -> np.ndarray:
    t = tt(2.2)
    f = 30 + 32 * np.exp(-t / 0.15)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.55)
    thud = lp(rng.standard_normal(len(t)), 260, 2) * np.exp(-t / 0.07) * 1.4
    crack = bp(rng.standard_normal(len(t)), 1500, 7000) * np.exp(-t / 0.025) * 0.4
    return reverb(np.tanh(1.5 * (sub + thud + crack)), HALL, 0.22)


def boom() -> np.ndarray:
    t = tt(3.6)
    f = 27 + 30 * np.exp(-t / 0.25)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 1.1)
    hit = lp(rng.standard_normal(len(t)), 2600, 2) * np.exp(-t / 0.12)
    ring = sum(np.sin(2 * np.pi * fr * t) for fr in (176, 263, 351, 527)) * np.exp(-t / 0.7) * 0.08
    return reverb(np.tanh(1.3 * (sub + 0.6 * hit + ring)), HALL, 0.35)


def tick() -> np.ndarray:
    t = tt(0.15)
    x = (np.sin(2 * np.pi * 2300 * t) + 0.35 * np.sin(2 * np.pi * 4600 * t)) * np.exp(-t / 0.012)
    x += hp(rng.standard_normal(len(t)), 5000) * np.exp(-t / 0.002) * 0.4
    return reverb(x, ROOM, 0.15)


def pop() -> np.ndarray:
    t = tt(0.35)
    f = 330 + 900 * np.exp(-t / 0.018)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.06)
    x += 0.3 * np.sin(2 * np.pi * np.cumsum(2 * f) / SR) * np.exp(-t / 0.03)
    return reverb(x, ROOM, 0.2)


def riser() -> np.ndarray:
    d = 2.0
    x = sweep_noise(d, lambda t: 250 * (40 ** np.clip(t / d, 0, 1)), 0.5, seed=5)
    t = tt(d)[: len(x)]
    tone = sum(np.sin(2 * np.pi * np.cumsum(hz(m) * 2 ** (t / d)) / SR) for m in (54, 61, 66)) / 3
    env = (t / d) ** 2.5
    y = (x * 1.2 + tone[:, None] * 0.5) * env[:, None]
    y[-int(0.006 * SR) :] *= np.linspace(1, 0, int(0.006 * SR))[:, None]
    return y


def swell() -> np.ndarray:
    hit = pad_chord([54, 61, 66, 69], 0.25, 4000)
    wet = reverb(hit, HALL, 1.0)[: int(1.3 * SR)]
    return wet[::-1] * 3.0


def glitch() -> np.ndarray:
    out = []
    for _ in range(14):
        d = rng.uniform(0.012, 0.03)
        t = tt(d)
        kind = rng.integers(0, 3)
        if kind == 0:
            s = np.sign(np.sin(2 * np.pi * rng.uniform(180, 2400) * t))
        elif kind == 1:
            s = rng.standard_normal(len(t))
        else:
            s = np.zeros(len(t))
        s = np.round(s * 4) / 4  # bit-crush
        p = rng.uniform(0, 1)
        out.append(np.stack([s * math.cos(p * math.pi / 2), s * math.sin(p * math.pi / 2)], 1))
    return np.concatenate(out) * 0.6


def main() -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    clean_voice()
    for name, fn in [
        ("whoosh", whoosh),
        ("impact", impact),
        ("boom", boom),
        ("tick", tick),
        ("pop", pop),
        ("riser", riser),
        ("swell", swell),
        ("glitch", glitch),
    ]:
        write(AUDIO / f"{name}.wav", peak_norm(stereo(fn())))
    m = music()
    write(AUDIO / "music.wav", m)
    print(f"music: {len(m) / SR:.1f}s, peak {20 * math.log10(np.max(np.abs(m))):.1f} dBFS")
    print("wrote", sorted(p.name for p in AUDIO.glob("*.wav")))


if __name__ == "__main__":
    main()
