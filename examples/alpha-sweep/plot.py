"""Plot final error vs alpha into outputs/plot.png — pure-stdlib PNG rasterizer (no matplotlib needed)."""
import glob
import json
import math
import struct
import sys
import zlib

W, H, M = 640, 400, 56


def png(path, px):
    raw = b"".join(b"\x00" + bytes(v for p in row for v in p) for row in px)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main(out):
    pts = []
    for s in glob.glob("outputs/alpha_*/summary.json"):
        d = json.load(open(s))
        pts.append((d["alpha"], d["final_error"]))
    pts.sort()
    px = [[(255, 255, 255)] * W for _ in range(H)]
    def put(x, y, c, r=1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if 0 <= x + dx < W and 0 <= y + dy < H and dx * dx + dy * dy <= r * r:
                    px[y + dy][x + dx] = c
    for x in range(M, W - M // 2):
        put(x, H - M, (60, 60, 60))
    for y in range(M // 2, H - M):
        put(M, y, (60, 60, 60))
    for i in range(1, 5):  # grid
        y = M // 2 + i * (H - M - M // 2) // 5
        for x in range(M + 1, W - M // 2, 4):
            put(x, y, (225, 225, 230))
    if pts:
        xs = [p[0] for p in pts]
        ys = [math.log10(max(p[1], 1e-12)) for p in pts]
        x0, x1 = min(xs) - 0.1, max(xs) + 0.1
        y0, y1 = min(ys) - 0.3, max(ys) + 0.3
        tx = lambda v: int(M + (v - x0) / (x1 - x0) * (W - M - M // 2 - M))
        ty = lambda v: int(H - M - (v - y0) / (y1 - y0) * (H - M - M // 2 - 10))
        prev = None
        for (a, _), ly in zip(pts, ys):
            p = (tx(a), ty(ly))
            if prev:
                n = max(abs(p[0] - prev[0]), abs(p[1] - prev[1]))
                for k in range(n + 1):
                    put(prev[0] + (p[0] - prev[0]) * k // max(n, 1), prev[1] + (p[1] - prev[1]) * k // max(n, 1), (91, 91, 214), 1)
            prev = p
        best = min(range(len(pts)), key=lambda i: pts[i][1])
        for i, ((a, _), ly) in enumerate(zip(pts, ys)):
            put(tx(a), ty(ly), (60, 207, 145) if i == best else (47, 128, 237), 6)
    png(out, px)
    print("wrote", out, "points:", pts)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "outputs/plot.png")
