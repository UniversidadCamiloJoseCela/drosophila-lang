#!/usr/bin/env python3
"""
gif_suma.py — genera el GIF del README: suma.dros con la entrada (3, 4) sobre el
cerebro FlyWire, vista frontal, un cuadro por paso de simulación.

Necesita Pillow además de las dependencias del proyecto (pip install pillow).

Uso (desde la raíz del proyecto, con el entorno activado):
    python scripts/gif_suma.py docs/suma.gif
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import drosophila as d  # noqa: E402
import visor3d  # noqa: E402

W, H = 720, 470
BRAIN_H = 380
BG = (13, 17, 23)

c = d.cargar_flywire(flujo=None)
pos = d.cargar_posiciones(c, flujo=None)
vm = d.DrosophilaVM((REPO / "suma.dros").read_text(encoding="utf-8"), cerebro=c, flujo_info=None)
res, cuadros, salidas = visor3d.registrar(vm, [3, 4], 10_000)

# Vista frontal: x a la derecha, -y hacia arriba, ajustada al panel del cerebro.
x, y = pos[:, 0], -pos[:, 1]
pad = 14
sx = (W - 2 * pad) / (x.max() - x.min())
sy = (BRAIN_H - 2 * pad) / (y.max() - y.min())
s = min(sx, sy)
ox = (W - s * (x.max() - x.min())) / 2
oy = (BRAIN_H - s * (y.max() - y.min())) / 2
px = np.clip((ox + (x - x.min()) * s).astype(int), 0, W - 1)
py = np.clip((BRAIN_H - (oy + (y - y.min()) * s)).astype(int), 0, BRAIN_H - 1)

base = np.zeros((BRAIN_H, W), np.float32)
np.add.at(base, (py, px), 1.0)
base = np.log1p(base) / np.log1p(base).max()
base_rgb = np.array(BG, np.float32) + base[..., None] * np.array([70, 80, 95], np.float32)

implante = vm.implante.es_implante
mono = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 15)
mono_s = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)

texto_por_t = {}
acum = ""
for t, b in salidas:
    acum += b.decode("utf-8", errors="replace")
    texto_por_t[t] = acum

heat = np.zeros((BRAIN_H, W), np.float32)
heat_imp = np.zeros((BRAIN_H, W), np.float32)
frames, durs = [], []
salida = ""
for t, idx in enumerate(cuadros):
    heat *= 0.55
    heat_imp *= 0.55
    nat = idx[~implante[idx]]
    imp = idx[implante[idx]]
    np.add.at(heat, (py[nat], px[nat]), 1.0)
    np.add.at(heat_imp, (py[imp], px[imp]), 1.0)
    salida = texto_por_t.get(t, salida)

    rgb = base_rgb.copy()
    h = np.clip(heat, 0, 1)[..., None]
    hi = np.clip(heat_imp, 0, 1)[..., None]
    rgb = rgb * (1 - h) + h * np.array([255, 170, 60], np.float32)
    rgb = rgb * (1 - hi) + hi * np.array([90, 220, 255], np.float32)
    img = Image.new("RGB", (W, H), BG)
    brain = Image.fromarray(rgb.astype(np.uint8))
    # Engorda los puntos activos para que una neurona suelta se vea en el GIF.
    glow = Image.fromarray((np.clip(heat + heat_imp, 0, 1) * 255).astype(np.uint8)).filter(
        ImageFilter.MaxFilter(3))
    col = Image.fromarray(rgb.astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
    brain.paste(col, mask=glow)
    img.paste(brain, (0, 0))

    dr = ImageDraw.Draw(img)
    for i in imp:
        dr.ellipse([px[i] - 3, py[i] - 3, px[i] + 3, py[i] + 3], fill=(90, 220, 255))
    if t == len(cuadros) - 1:
        dr.text((W // 2, BRAIN_H - 22), "✝ disparó la Fibra Gigante", font=mono_s,
                fill=(230, 120, 120), anchor="ma")
    dr.text((14, 10), "FlyWire v783 · 138 639 neuronas", font=mono_s, fill=(140, 150, 165))
    dr.text((W - 14, 10), f"t = {t:3d}", font=mono_s, fill=(140, 150, 165), anchor="ra")
    dr.rectangle([0, BRAIN_H, W, H], fill=(22, 27, 34))
    dr.text((14, BRAIN_H + 14), '$ ./mosca.sh suma.dros --olor "3 4"', font=mono, fill=(200, 210, 220))
    dr.text((14, BRAIN_H + 42), salida.rstrip("\n") + ("" if t == len(cuadros) - 1 else "▌"),
            font=mono, fill=(120, 230, 140))
    dr.text((W - 14, BRAIN_H + 66), "● programa  ● cerebro natural", font=mono_s,
            fill=(140, 150, 165), anchor="ra")
    bb = dr.textbbox((W - 14, BRAIN_H + 66), "● programa  ● cerebro natural", font=mono_s, anchor="ra")
    dr.text((bb[0], BRAIN_H + 66), "●", font=mono_s, fill=(90, 220, 255))
    off = dr.textlength("● programa  ", font=mono_s)
    dr.text((bb[0] + off, BRAIN_H + 66), "●", font=mono_s, fill=(255, 170, 60))
    frames.append(img)
    durs.append(110)

durs[-1] = 3000
out = Path(sys.argv[1])
frames[0].save(out, save_all=True, append_images=frames[1:], duration=durs, loop=0, optimize=True)
print(out, out.stat().st_size // 1024, "KiB", len(frames), "frames")
