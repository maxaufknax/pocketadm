#!/usr/bin/env python3
"""The preview's soundtrack, synthesised from nothing so it carries no licence.

    python3 music.py timeline.json soundtrack.wav

timeline.json comes from preview.html (render.mjs writes it): the scene cuts,
taps, keystrokes and the notification, so every whoosh and click lands on its
frame. 120 bpm with the bar grid shifted half a second, so the downbeats fall
on the cuts at 2.5, 6.5, 12.5, 18.5, 24.5 and 26.5 s. Chords: Dm9 - Bbmaj9 -
Fadd9 - C, resolving to Fmaj9 for the logo. Stereo, 48 kHz, 16 bit.

Needs numpy and scipy.
"""
from __future__ import annotations

import json
import sys
import wave

import numpy as np
from scipy import signal

SR = 48000
tl = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
DUR = float(tl["duration"])
N = int((DUR + 1.5) * SR)
BEAT = 60.0 / tl["bpm"]
BAR = 4 * BEAT
OFF = float(tl["gridOffset"])
SC = tl["scenes"]
rng = np.random.default_rng(11)


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def bus() -> np.ndarray:
    return np.zeros((N, 2))


def pan2(x: np.ndarray, pan: float) -> np.ndarray:
    a = (pan + 1) * np.pi / 4
    return np.stack([x * np.cos(a), x * np.sin(a)], axis=1) * np.sqrt(2)


def put(dst: np.ndarray, x: np.ndarray, t: float, gain: float = 1.0, pan: float = 0.0) -> None:
    if x.ndim == 1:
        x = pan2(x, pan)
    i = int(round(t * SR))
    if i < 0:
        x, i = x[-i:], 0
    j = min(N, i + len(x))
    if j > i:
        dst[i:j] += x[: j - i] * gain


def tt(sec: float) -> np.ndarray:
    return np.arange(int(sec * SR)) / SR


def env_adsr(n: int, a: float, r: float) -> np.ndarray:
    e = np.ones(n)
    na, nr = max(1, int(a * SR)), max(1, int(r * SR))
    e[:na] = np.linspace(0, 1, na) ** 1.5
    e[-nr:] *= np.linspace(1, 0, nr) ** 1.2
    return e


def lp(x, fc, order=2):
    return signal.sosfilt(signal.butter(order, min(fc, SR * 0.45), "low", fs=SR, output="sos"), x, axis=0)


def hp(x, fc, order=2):
    return signal.sosfilt(signal.butter(order, fc, "high", fs=SR, output="sos"), x, axis=0)


def bp(x, lo, hi, order=2):
    return signal.sosfilt(signal.butter(order, [lo, min(hi, SR * 0.45)], "band", fs=SR, output="sos"), x, axis=0)


def svf_sweep(x: np.ndarray, f0: float, f1: float, q: float = 1.2, curve: float = 2.0) -> np.ndarray:
    """Band-pass whose centre glides from f0 to f1 (Chamberlin state variable)."""
    n = len(x)
    fc = f0 * (f1 / f0) ** (np.linspace(0, 1, n) ** curve)
    f = 2 * np.sin(np.pi * fc / SR)
    y = np.empty(n)
    low = band = 0.0
    damp = 1.0 / q
    for i in range(n):
        high = x[i] - low - damp * band
        band += f[i] * high
        low += f[i] * band
        y[i] = band
    return y


# ----------------------------------------------------------------- voices
def saw(f: float, t: np.ndarray, phase: float) -> np.ndarray:
    """A sawtooth without most of its aliasing (PolyBLEP)."""
    dt = f / SR
    ph = (f * t + phase) % 1.0
    y = 2 * ph - 1
    a = ph < dt
    x = ph[a] / dt
    y[a] -= 2 * x - x * x - 1
    b = ph > 1 - dt
    x = (ph[b] - 1) / dt
    y[b] -= x * x + 2 * x + 1
    return y


def supersaw(freq: float, sec: float, detune=(-14, -7, 0, 7, 14)) -> np.ndarray:
    t = tt(sec)
    out = np.zeros((len(t), 2))
    for k, c in enumerate(detune):
        out += pan2(saw(freq * 2 ** (c / 1200), t, rng.random()), (k / (len(detune) - 1) - 0.5) * 1.2)
    return out / len(detune)


def pluck(freq: float, sec: float = 0.4, decay: float = 0.11) -> np.ndarray:
    t = tt(sec)
    x = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) + 0.12 * np.sin(6 * np.pi * freq * t)
    e = np.exp(-t / decay) * np.minimum(1, t / 0.003)
    return x * e


def bell(freq: float, sec: float = 1.6) -> np.ndarray:
    t = tt(sec)
    x = (np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.55)
         + 0.45 * np.sin(2 * np.pi * freq * 2.0 * t) * np.exp(-t / 0.3)
         + 0.25 * np.sin(2 * np.pi * freq * 3.01 * t) * np.exp(-t / 0.16)
         + 0.12 * np.sin(2 * np.pi * freq * 5.4 * t) * np.exp(-t / 0.07))
    return x * np.minimum(1, t / 0.002)


def kick() -> np.ndarray:
    t = tt(0.5)
    f = 44 + 92 * np.exp(-t / 0.042)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.3)
    x += rng.standard_normal(len(t)) * np.exp(-t / 0.004) * 0.25
    return np.tanh(1.6 * x) * 0.9


def clap() -> np.ndarray:
    t = tt(0.45)
    n = bp(rng.standard_normal(len(t)), 1100, 6500)
    e = np.exp(-t / 0.13)
    for d in (0.0, 0.011, 0.022):           # the claps of a clap
        e += 0.7 * np.exp(-np.maximum(0, t - d) / 0.008) * (t >= d)
    body = np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.05) * 0.4
    return n * e * 0.55 + body


def hat(open_: bool = False) -> np.ndarray:
    t = tt(0.25 if open_ else 0.08)
    return hp(rng.standard_normal(len(t)), 7200) * np.exp(-t / (0.09 if open_ else 0.022))


def crash(sec=2.6) -> np.ndarray:
    t = tt(sec)
    n = hp(rng.standard_normal((len(t), 2)), 3800)
    return n * np.exp(-t / 0.8)[:, None] * np.minimum(1, t / 0.002)[:, None]


def boom() -> np.ndarray:
    t = tt(2.2)
    f = 38 + 30 * np.exp(-t / 0.15)
    return np.tanh(1.3 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.75))


# ----------------------------------------------------------------- harmony
CHORDS = {
    "Dm9": [50, 53, 57, 60, 64], "Bbmaj9": [46, 50, 53, 57, 60],
    "Fadd9": [53, 57, 60, 65, 67], "C": [52, 55, 60, 62, 67], "Fmaj9": [41, 53, 57, 60, 64, 67],
}
ROOT = {"Dm9": 38, "Bbmaj9": 34, "Fadd9": 41, "C": 36, "Fmaj9": 29}
LOOP = ["Dm9", "Bbmaj9", "Fadd9", "C"]
N_BARS = int(round((SC["outro"][0] - OFF) / BAR))           # bars 0 .. N_BARS-1, then the outro chord


def bar_start(b: int) -> float:
    return OFF + b * BAR


def chord_of(b: int) -> str:
    return "Dm9" if b == 0 else LOOP[(b - 1) % 4]


pad, bass, arp, drums, fx, send = bus(), bus(), bus(), bus(), bus(), bus()
kicks: list[float] = []

# pad: one chord a bar; the filter opens as the video goes on
for b in range(N_BARS):
    name, t0 = chord_of(b), bar_start(b)
    sec = BAR + 0.35
    cutoff = 520 if b == 0 else 1300 if b < 3 else 2100 if b < 9 else 2600
    attack = 1.6 if b == 0 else 0.05
    start = 0.0 if b == 0 else t0
    if b == 0:
        sec = bar_start(1) - 0.0 + 0.35
    chord = sum(supersaw(mtof(m), sec) for m in CHORDS[name]) / len(CHORDS[name])
    chord = lp(chord, cutoff, 4) * env_adsr(len(chord), attack, 0.35)[:, None]
    put(pad, chord, start, 0.55 if b else 0.5)
# the resolution under the logo
t0 = SC["outro"][0]
fin = sum(supersaw(mtof(m), 4.0) for m in CHORDS["Fmaj9"]) / 6
fin = lp(fin, 3000, 4) * (np.exp(-tt(4.0) / 1.6) * np.minimum(1, tt(4.0) / 0.01))[:, None]
put(pad, fin, t0, 0.75)

# bass: held roots at first, eighths once the drums are in
for b in range(1, N_BARS):
    f = mtof(ROOT[chord_of(b)])
    t0 = bar_start(b)
    if b < 3:
        t = tt(BAR + 0.05)
        x = (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)) * env_adsr(len(t), 0.02, 0.1)
        put(bass, x, t0, 0.5)
    else:
        for k in range(8):
            t = tt(BEAT / 2)
            x = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t) + 0.08 * np.sin(6 * np.pi * f * t)
            x *= np.exp(-t / 0.16) * np.minimum(1, t / 0.004)
            put(bass, np.tanh(1.4 * x), t0 + k * BEAT / 2, 0.42 if k % 2 else 0.3)
t = tt(3.0)
put(bass, np.sin(2 * np.pi * mtof(29) * t) * np.exp(-t / 1.2) * np.minimum(1, t / 0.01), SC["outro"][0], 0.55)

# arpeggio: sixteenths over the chord, an octave up, with a dotted-eighth echo
PATTERN = [0, 2, 1, 3, 2, 4, 3, 1]
for b in range(1, N_BARS):
    tones = sorted(m + 12 for m in CHORDS[chord_of(b)])
    level = 0.1 if b < 3 else 0.17 if b < 5 else 0.22
    for k in range(16):
        tk = bar_start(b) + k * BEAT / 4
        if tk >= SC["outro"][0] - 0.05:
            break
        m = tones[PATTERN[k % 8] % len(tones)]
        vel = 1.0 if k % 4 == 0 else 0.7
        x = pluck(mtof(m), 0.35, 0.09 if b < 5 else 0.12)
        x = lp(x, 4200)
        p = 0.35 * np.sin(k * 0.9)
        put(arp, x, tk, level * vel, p)
        put(arp, x, tk + 0.375, level * vel * 0.32, -0.7)
        put(arp, x, tk + 0.75, level * vel * 0.16, 0.7)

# drums
K, CL, HC, HO = kick(), clap(), hat(), hat(True)
for b in range(1, N_BARS):
    t0 = bar_start(b)
    last = b == N_BARS - 1
    for beat in range(4):
        tb = t0 + beat * BEAT
        half = b < 3 and beat in (1, 3)
        quiet_watch = b == 9 and beat < 2           # room for the notification
        if not half and not (last and beat == 3):
            put(drums, K, tb, 0.95)
            kicks.append(tb)
        if b >= 3 and beat in (1, 3) and not quiet_watch and not last:
            put(drums, CL, tb, 0.42)
            put(send, CL, tb, 0.25)
        # hats
        if b >= 2:
            put(drums, HO if (b >= 5 and beat == 3) else HC, tb + BEAT / 2, 0.16 if b < 5 else 0.2, 0.25)
        if b >= 5 and not quiet_watch:
            put(drums, HC, tb + BEAT / 4, 0.07, -0.3)
            put(drums, HC, tb + 3 * BEAT / 4, 0.08, -0.3)
    if last:                                         # a roll into the logo
        for k in range(8):
            tr = t0 + 2 * BEAT + k * BEAT / 4
            put(drums, CL, tr, 0.12 + 0.05 * k)
            put(send, CL, tr, 0.08)

# sidechain: pad, bass and arp breathe with the kick
duck = np.ones(N)
dl = int(0.3 * SR)
shape = 1 - 0.55 * np.exp(-np.arange(dl) / SR / 0.12)
shape[: int(0.004 * SR)] = np.linspace(1, shape[int(0.004 * SR)], int(0.004 * SR))
for tk in kicks:
    i = int(tk * SR)
    j = min(N, i + dl)
    duck[i:j] = np.minimum(duck[i:j], shape[: j - i])
pad *= duck[:, None]
bass *= (0.35 + 0.65 * duck)[:, None]
arp *= (0.6 + 0.4 * duck)[:, None]

# ----------------------------------------------------------------- effects
# the riser into the first downbeat
r = tt(SC["dash"][0] - 0.08)
noise = rng.standard_normal(len(r))
rise = svf_sweep(noise, 250, 7000, 1.6, 1.6) * (r / r[-1]) ** 2.2 * 0.6
put(fx, pan2(rise, 0), 0.0, 1.0)
put(send, pan2(rise, 0), 0.0, 0.4)
# impacts: the dashboard's downbeat and the logo
for ti in (SC["dash"][0], SC["outro"][0]):
    put(fx, crash(), ti, 0.28)
    put(fx, boom(), ti, 0.7)
    put(send, crash(), ti, 0.2)
# whooshes into the other cuts
for key in ("apps", "term", "chat", "watch", "health", "themes"):
    tc = SC[key][0]
    L = 0.42
    x = svf_sweep(rng.standard_normal(int(L * SR)), 500, 6500, 1.4, 1.3)
    e = np.linspace(0, 1, len(x)) ** 2.4
    e[-int(0.04 * SR):] *= np.linspace(1, 0, int(0.04 * SR))
    x *= e
    st = np.stack([x * np.linspace(1.2, 0.5, len(x)), x * np.linspace(0.5, 1.2, len(x))], 1)
    put(fx, st, tc - L + 0.02, 0.32)
    put(send, st, tc - L + 0.02, 0.15)
# taps
for td in tl["taps"]:
    t = tt(0.05)
    x = (np.sin(2 * np.pi * 1900 * t) + 0.4 * np.sin(2 * np.pi * 3800 * t)) * np.exp(-t / 0.007)
    put(fx, x, td, 0.14)
# keystrokes in the terminal
for k in tl["keys"]:
    n = max(1, int(k["n"]))
    for i in range(n + 1):
        tk = k["t0"] + (k["t1"] - k["t0"]) * i / n
        t = tt(0.03)
        x = bp(rng.standard_normal(len(t)), 1800, 6000) * np.exp(-t / 0.005)
        put(fx, x, tk, 0.07 if i < n else 0.11, float(rng.uniform(-0.3, 0.3)))
# the notification
tb = float(tl["banner"]) + 0.08
for i, m in enumerate((81, 88)):
    put(fx, bell(mtof(m), 1.4), tb + i * 0.11, 0.2, 0.15)
    put(send, bell(mtof(m), 1.4), tb + i * 0.11, 0.18)
# a glass note for every new theme
TH0, THD = tl["themes"]["t0"], tl["themes"]["step"]
for i, m in enumerate((77, 81, 84, 86, 89)[: tl["themes"]["n"]]):
    put(fx, bell(mtof(m), 1.2), TH0 + i * THD, 0.11, (i - 2) * 0.3)
    put(send, bell(mtof(m), 1.2), TH0 + i * THD, 0.14)
# sparkles when the icon appears
for t0, notes in ((0.3, (77, 81, 84, 89)), (SC["outro"][0] + 0.12, (77, 81, 84, 89, 93)), (SC["outro"][0] + 1.85, (89, 93, 96))):
    for i, m in enumerate(notes):
        put(fx, bell(mtof(m), 1.5), t0 + i * 0.055, 0.09, (i % 2 - 0.5) * 0.6)
        put(send, bell(mtof(m), 1.5), t0 + i * 0.055, 0.14)

# ----------------------------------------------------------------- mix
ir_t = tt(2.6)
ir = rng.standard_normal((len(ir_t), 2)) * np.exp(-ir_t / 0.42)[:, None]
ir = lp(ir, 6500)
ir[: int(0.018 * SR)] = 0
ir /= np.sqrt((ir ** 2).sum(axis=0))
send += pad * 0.35 + arp * 0.5
wet = np.stack([signal.fftconvolve(send[:, c], ir[:, c])[:N] for c in range(2)], 1)

GAIN = {"pad": 2.0, "bass": 0.7, "arp": 1.4, "drums": 0.55, "fx": 1.0, "reverb": 0.55}
mix = pad * GAIN["pad"] + bass * GAIN["bass"] + arp * GAIN["arp"] + drums * GAIN["drums"] + fx * GAIN["fx"] + wet * GAIN["reverb"]
for name, x in (("pad", pad), ("bass", bass), ("arp", arp), ("drums", drums), ("fx", fx), ("reverb", wet)):
    x = x * GAIN[name]
    print(f"  {name:7s} rms {20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12):6.1f} dB")
mix = hp(mix, 28)
mix /= np.max(np.abs(mix)) + 1e-9
mix = np.tanh(1.5 * mix) / np.tanh(1.5)
# fade in the last half second, trim to the video
fade = np.ones(N)
a, b = int((DUR - 0.55) * SR), int(DUR * SR)
fade[a:b] = np.linspace(1, 0, b - a) ** 1.5
fade[b:] = 0
mix = (mix * fade[:, None])[: int(DUR * SR)]
mix = lp(mix, 17000, 4)
# true peak (4x oversampled) at -1.5 dBTP, so the AAC encoder never clips
true_peak = np.max(np.abs(signal.resample_poly(mix, 4, 1, axis=0)))
mix *= 10 ** (-1.5 / 20) / true_peak

pcm = (mix * 32767).astype("<i2")
with wave.open(OUT, "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print(f"{OUT}: {len(mix) / SR:.2f}s, peak {np.max(np.abs(mix)):.2f}, rms {20 * np.log10(np.sqrt(np.mean(mix ** 2))):.1f} dBFS")
