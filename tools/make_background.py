"""Render the FilingCheck background: a blue night sky with stars, low mist and a warm beam of light.

The same image is used by the website (static/bg.jpg) and for presentation slides (assets/slides/).
Fully procedural and seeded, so it can be regenerated exactly:

    venv/bin/python tools/make_background.py
"""
import os

import numpy as np
from PIL import Image, ImageFilter

W, H = 3840, 2160
rng = np.random.default_rng(218)


def noise(scale_x, scale_y, octaves=5, persistence=.55):
    """Fractal value noise in [0, 1]: random grids upsampled smoothly and summed."""
    out = np.zeros((H, W), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        gw, gh = max(2, int(scale_x * 2 ** o)), max(2, int(scale_y * 2 ** o))
        grid = Image.fromarray((rng.random((gh, gw)) * 255).astype(np.uint8))
        out += amp * np.asarray(grid.resize((W, H), Image.BICUBIC), np.float32) / 255
        total += amp
        amp *= persistence
    return out / total


def blur(a, radius):
    """Gaussian blur of a float array (PIL only blurs 8-bit images, so scale through L mode)."""
    m = float(a.max()) or 1.0
    im = Image.fromarray((np.clip(a / m, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(im, np.float32) / 255 * m


def gauss(v, mu, sigma):
    return np.exp(-((v - mu) / sigma) ** 2)


def smooth(e0, e1, v):
    t = np.clip((v - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


y, x = np.mgrid[0:H, 0:W].astype(np.float32)
y /= H
x /= W
aspect = W / H

# 1. Sky: deep blue overhead, near-black at the horizon line and below.
top, mid, low = np.array([.050, .090, .165]), np.array([.030, .055, .100]), np.array([.010, .014, .024])
t1, t2 = smooth(0, .55, y)[..., None], smooth(.55, 1, y)[..., None]
img = top * (1 - t1) + mid * t1
img = img * (1 - t2) + low * t2
# moonlit glow high in the middle
glow = np.exp(-(((x - .46) * aspect / .9) ** 2 + ((y + .05) / .55) ** 2))
img += glow[..., None] * np.array([.10, .16, .27]) * .9

# 2. Stars, mostly in the upper sky, varied in size and brightness.
stars = np.zeros((H, W), np.float32)
n = 2600
sx = rng.integers(0, W, n)
sy = (rng.random(n) ** 1.6 * H * .78).astype(int)
stars[sy, sx] = rng.random(n) ** 3.2 * 1.6
big = rng.random(n) > .97
stars[sy[big], sx[big]] *= 2.2
star_layer = blur(stars, 1.1) * 3.2
twinkle = .55 + .45 * noise(6, 4, 2)

# 3. Mist: a low bank of cloud plus thin wisps higher up.
fbm = noise(5, 3.2, 6)
wisps = noise(3, 7, 5)
bank = gauss(y, .8, .17)
mist = np.clip((fbm - .40) * 2.2, 0, 1) * bank * .8 + np.clip((wisps - .58) * 2.2, 0, 1) * gauss(y, .3, .18) * .12
img = img * (1 - mist[..., None] * .3) + mist[..., None] * np.array([.20, .27, .38])
img += (star_layer * twinkle * (1 - np.clip(mist * 2.2, 0, 1)))[..., None] * np.array([.85, .9, 1.0])

# 4. A warm shaft of light on the right: a soft cone that widens as it falls, with volumetric streaks.
x0 = .78 + (y - .5) * .06
streaks = noise(55, 1.2, 3, .5)
beam_v = (1 - y) ** .35 * (1 - smooth(.55, 1.05, y) * .85)
core = gauss(x, x0, .018 + .022 * y)
halo = gauss(x, x0, .065 + .07 * y) * .55
wide = gauss(x, x0, .22 + .1 * y) * .18
vol = .45 + 1.1 * streaks
img += (core * beam_v * vol)[..., None] * np.array([1.0, .80, .55]) * .62
img += ((halo + wide) * beam_v * vol)[..., None] * np.array([1.0, .50, .20]) * .62
# the mist catches the beam where they meet
img += (mist * gauss(x, x0, .16) * (.4 + beam_v))[..., None] * np.array([1.0, .52, .24]) * .8
# a faint cool counter-light on the far left keeps the frame balanced
img += (gauss(x, .06, .12) * gauss(y, .45, .35))[..., None] * np.array([.05, .10, .16]) * .7

# 5. Bloom, vignette, tone map, grain.
lum = np.clip(img.max(axis=2) - .35, 0, None)
bloom = blur(lum, 60)
img += bloom[..., None] * np.array([1.0, .7, .45]) * .8
r2 = ((x - .5) * 1.05) ** 2 + ((y - .42) * 1.0) ** 2
img *= (1 - .6 * np.clip(r2, 0, 1))[..., None]
img = img / (1 + img * .55)
img = np.clip(img, 0, 1) ** (1 / 1.08)
img += rng.normal(0, .010, (H, W, 1)).astype(np.float32)
out = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(f"{root}/static", exist_ok=True)
os.makedirs(f"{root}/assets/slides", exist_ok=True)
out.resize((2560, 1440), Image.LANCZOS).save(f"{root}/static/bg.jpg", quality=88, optimize=True, progressive=True)
out.save(f"{root}/assets/slides/filingcheck-background-3840x2160.png", optimize=True)
out.resize((1920, 1080), Image.LANCZOS).save(f"{root}/assets/slides/filingcheck-background-1920x1080.png", optimize=True)
print("wrote static/bg.jpg and assets/slides/*.png")
