"""Synthetic 1-D diffusion: writes a real PNG figure, a CSV table and a Quarto report. Pure standard library."""
import math, os, struct, zlib

os.makedirs("reports/quarto/chapters", exist_ok=True)
os.makedirs("reports/quarto/figures", exist_ok=True)
W, H = 120, 80
rows = []
for y in range(H):
    t = 0.05 + 0.5 * y / H
    rows.append(b"\x00" + bytes(int(255 * math.exp(-((x - W / 2) / (W * t * 0.6)) ** 2)) for x in range(W)))
raw = zlib.compress(b"".join(rows))
def chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 0, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
open("reports/quarto/figures/diffusion.png", "wb").write(png)
open("reports/quarto/figures/widths.csv", "w").write("t,width\n" + "\n".join(f"{t},{math.sqrt(t):.4f}" for t in (1, 2, 4)) + "\n")
open("reports/quarto/chapters/00-smoke.qmd", "w").write("""# Diffusion smoke test

The width of a spreading spike grows with the square root of time.

```{python}
import numpy as np   # would run the simulation: the website never executes it
```

![Temperature against position as time advances downward: the spike spreads and flattens, as the diffusion equation predicts.](../figures/diffusion.png){#fig-diffusion fig-alt="Spreading spike"}

As @fig-diffusion shows, the spike widens with time.
""")
