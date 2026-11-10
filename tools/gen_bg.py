# -*- coding: utf-8 -*-
"""Painted, seamlessly tiling nebula backdrops for the HD pack (one per background theme)."""
import sys, colorsys
import numpy as np
from PIL import Image

N = int(sys.argv[2]) if len(sys.argv) > 2 else 1536
OUT = sys.argv[1]
ONLY = sys.argv[3].split(',') if len(sys.argv) > 3 else None

fy = np.fft.fftfreq(N)[:, None] * N
fx = np.fft.rfftfreq(N)[None, :] * N
FR = np.sqrt(fx * fx + fy * fy); FR[0, 0] = 1.0

def noise(rng, beta, lo=1.0, hi=None):
    """periodic fractal noise: random phases, 1/f^beta amplitude → tiles by construction"""
    ph = rng.uniform(0, 2 * np.pi, FR.shape)
    amp = FR ** (-beta)
    amp[FR < lo] = 0
    if hi: amp *= np.exp(-(FR / hi) ** 2)
    amp[0, 0] = 0
    amp[FR > N * 0.46] = 0  # nothing at the Nyquist edge: no faint stripes
    f = np.fft.irfft2(amp * np.exp(1j * ph), s=(N, N))
    f -= f.mean(); f /= f.std() + 1e-9
    return f.astype(np.float32)

YY, XX = np.mgrid[0:N, 0:N].astype(np.float32)
def warp(f, wx, wy):
    x = (XX + wx) % N; y = (YY + wy) % N
    xf = np.floor(x); yf = np.floor(y)
    tx = x - xf; ty = y - yf
    x0 = xf.astype(np.int32) % N; y0 = yf.astype(np.int32) % N
    x1 = (x0 + 1) % N; y1 = (y0 + 1) % N
    return (f[y0, x0] * (1 - tx) + f[y0, x1] * tx) * (1 - ty) + (f[y1, x0] * (1 - tx) + f[y1, x1] * tx) * ty

def blur(img, sigma):
    """periodic gaussian blur through the FFT"""
    g = np.exp(-2 * (np.pi * sigma) ** 2 * ((fx / N) ** 2 + (fy / N) ** 2))
    return np.fft.irfft2(np.fft.rfft2(img) * g, s=(N, N)).astype(np.float32)

def hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return np.array([r, g, b], dtype=np.float32)

def smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)

THEMES = {
    # id: (hues, strength, base rgb, saturation, star tint)
    'cosmos':  ([215, 255, 195, 230], 1.00, (4, 5, 10), 0.80),
    'abyss':   ([230, 250, 240, 220], 0.38, (1, 1, 3), 0.55),
    'neon':    ([300, 190, 280, 320], 1.00, (8, 2, 15), 0.95),
    'nebula':  ([280, 200, 330, 160], 1.35, (5, 3, 12), 0.85),
    'ocean':   ([190, 210, 175, 200], 1.10, (1, 7, 12), 0.85),
    'sunset':  ([18, 330, 285, 40], 1.15, (13, 4, 8), 0.90),
    'code':    ([130, 150, 110, 140], 0.80, (1, 6, 3), 0.85),
    'aurora':  ([160, 200, 280, 140], 0.95, (2, 4, 10), 0.85),
    'galaxy':  ([260, 300, 220, 280], 1.05, (3, 2, 8), 0.85),
    'crimson': ([350, 330, 10, 300], 1.10, (10, 1, 4), 0.90),
    'ice':     ([195, 185, 210, 175], 1.00, (2, 10, 16), 0.70),
    'toxic':   ([95, 120, 75, 150], 1.00, (4, 9, 2), 0.90),
    'royal':   ([45, 270, 40, 290], 1.00, (6, 3, 12), 0.85),
    'storm':   ([240, 265, 220, 285], 1.05, (3, 3, 12), 0.80),
    # 4.0 themes
    'ember':   ([20, 40, 5, 350], 1.10, (10, 3, 1), 0.92),
    'candy':   ([320, 185, 50, 275], 1.05, (9, 4, 12), 0.80),
    'mono':    ([220, 220, 220, 220], 0.85, (3, 3, 4), 0.06),
}

def make(tid):
    hues, strength, base, sat = THEMES[tid]
    rng = np.random.default_rng(sum(ord(c) * (i + 7) for i, c in enumerate(tid)) * 7919)
    # a gentle large-scale warp gives the gas its flow without turning it into marble
    wx = noise(rng, 2.8, hi=6) * (N * 0.035); wy = noise(rng, 2.8, hi=6) * (N * 0.035)
    rgb = np.zeros((N, N, 3), dtype=np.float32)
    for c in range(3): rgb[..., c] = (base[c] / 255.0) * 0.6
    # where there is gas at all: wide regions, the rest stays deep space
    region = smooth(warp(noise(rng, 2.7, hi=5), wx, wy), -0.9, 1.5)
    for i, h in enumerate(hues):
        mask = smooth(warp(noise(rng, 2.6, hi=6), wx, wy), -0.7, 1.6)          # where this colour lives
        f = warp(noise(rng, 2.25, lo=1.5), wx * 1.4, wy * 1.4)                    # the cloud itself, soft fBm
        d = np.clip(f * 0.38 + 0.42, 0, 1) ** 2.6
        fil = warp(noise(rng, 2.0, lo=4), wx * 1.8, wy * 1.8)
        wisps = np.clip(1 - np.abs(fil) * 1.5, 0, 1) ** 5                         # thin bright filaments inside the cloud
        dens = (d * (0.75 + 0.9 * wisps)) * mask * (0.35 + 0.65 * region)
        col = hsl(h + (i - 1.5) * 5, sat, 0.5)
        hot = hsl(h, sat * 0.5, 0.74)
        rgb += dens[..., None] * col[None, None, :] * (0.50 * strength)
        rgb += (dens ** 2.2)[..., None] * hot[None, None, :] * (0.55 * strength)
    # dust: soft dark veins
    dust = warp(noise(rng, 2.2, lo=2), wx * 2, wy * 2)
    lane = np.clip(1 - np.abs(dust) * 1.7, 0, 1) ** 3
    lane = blur(lane, 1.2) * smooth(warp(noise(rng, 2.5, hi=8), wx, wy), -0.3, 1.2)
    rgb *= (1 - 0.5 * lane)[..., None]
    rgb *= (1 + 0.06 * noise(rng, 1.2, lo=30))[..., None]                         # grain of the gas
    # stars: a faint dense field and a handful of bright ones with a soft glow
    stars = np.zeros((N, N, 3), dtype=np.float32)
    n_f = int(N * N * 0.0009)
    ys = rng.integers(0, N, n_f); xs = rng.integers(0, N, n_f)
    br = np.minimum(rng.pareto(2.8, n_f).astype(np.float32) * 0.16 + 0.03, 1.2)
    tint = np.stack([hsl(hues[int(k)] + rng.uniform(-25, 25), 0.3 if sat > 0.3 else 0.02, 0.86) for k in rng.integers(0, len(hues), 64)])
    np.add.at(stars, (ys, xs), br[:, None] * tint[rng.integers(0, 64, n_f)])
    glow = np.zeros((N, N, 3), dtype=np.float32)
    n_b = int(N * N * 0.000022)
    yb = rng.integers(0, N, n_b); xb = rng.integers(0, N, n_b)
    np.add.at(glow, (yb, xb), rng.uniform(0.4, 1.3, n_b).astype(np.float32)[:, None] * tint[rng.integers(0, 64, n_b)])
    for c in range(3):
        stars[..., c] = blur(stars[..., c], 0.6) * 2.4 + blur(glow[..., c], 1.2) * 7 + blur(glow[..., c], 5.0) * 26
    rgb += stars * (1 - 0.6 * np.clip(lane, 0, 1))[..., None]
    rgb = np.clip(rgb, 0, None)
    out = 1 - np.exp(-rgb * 1.25)
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)

for tid in THEMES:
    if ONLY and tid not in ONLY: continue
    im = Image.fromarray(make(tid), 'RGB')
    im.save(f'{OUT}/{tid}.jpg', quality=90, subsampling=0, optimize=True)
    a = np.asarray(im).astype(np.float32) / 255
    print(tid, 'mean', round(float(a.mean()), 3), 'p99', round(float(np.percentile(a, 99)), 3))
