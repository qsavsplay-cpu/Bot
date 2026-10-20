"""PNG/JPG/WEBP -> векторная группа Lottie (трассировка по цветам). Растр в TGS вставлять нельзя."""
import io

import numpy as np
from PIL import Image

from lottie_text import transform


def _remove_bg(rgb, alpha, tol=48):
    """Если у картинки нет прозрачности — убираем однотонный фон, связанный с краем."""
    h, w = alpha.shape
    corners = np.array([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]], dtype=float)
    bg = np.median(corners, axis=0)
    if np.abs(corners - bg).max() > tol:        # углы разного цвета — фон не однотонный
        return alpha
    near = np.sqrt(((rgb.astype(float) - bg) ** 2).sum(axis=2)) < tol
    seed = np.zeros_like(near)
    seed[0, :] = seed[-1, :] = seed[:, 0] = seed[:, -1] = True
    cur = seed & near
    for _ in range(h + w):
        p = np.pad(cur, 1)
        grown = (p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:] | cur) & near
        if (grown == cur).all():
            break
        cur = grown
    return alpha & ~cur


def _loops(mask):
    """Контуры бинарной маски (границы пикселей), дырки идут в обратном направлении."""
    m = np.pad(mask, 1)
    H, W = m.shape
    edges = {}

    def add(a, b):
        edges.setdefault(a, []).append(b)

    ys, xs = np.nonzero(m[1:-1, 1:-1])
    for y, x in zip(ys + 1, xs + 1):
        X, Y = int(x) - 1, int(y) - 1
        x, y = int(x), int(y)
        if not m[y - 1, x]: add((X, Y), (X + 1, Y))
        if not m[y, x + 1]: add((X + 1, Y), (X + 1, Y + 1))
        if not m[y + 1, x]: add((X + 1, Y + 1), (X, Y + 1))
        if not m[y, x - 1]: add((X, Y + 1), (X, Y))
    loops = []
    while edges:
        start = next(iter(edges))
        cur, loop = start, [start]
        while True:
            nxt = edges[cur].pop()
            if not edges[cur]:
                del edges[cur]
            if nxt == start:
                break
            loop.append(nxt)
            cur = nxt
            if cur not in edges:
                break
        loops.append(loop)
    return loops


def _area(p):
    return 0.5 * sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))


def _rdp(pts, eps):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    n = (dx * dx + dy * dy) ** 0.5 or 1e-9
    dmax, idx = 0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / n
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _rdp(pts[:idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def _simplify_closed(p, eps):
    if len(p) < 6:
        return p
    far = max(range(len(p)), key=lambda i: (p[i][0] - p[0][0]) ** 2 + (p[i][1] - p[0][1]) ** 2)
    a = _rdp(p[:far + 1], eps)
    b = _rdp(p[far:] + [p[0]], eps)
    return a[:-1] + b[:-1]


def _bezier(poly):
    """Замкнутый многоугольник -> гладкие кубические безье (Catmull-Rom с ограничением ручек)."""
    n = len(poly)
    v, i_, o_ = [], [], []
    for k in range(n):
        p0, p, p1 = poly[k - 1], poly[k], poly[(k + 1) % n]
        tx, ty = (p1[0] - p0[0]) / 6, (p1[1] - p0[1]) / 6
        l_in = ((p[0] - p0[0]) ** 2 + (p[1] - p0[1]) ** 2) ** 0.5 / 3
        l_out = ((p1[0] - p[0]) ** 2 + (p1[1] - p[1]) ** 2) ** 0.5 / 3
        tl = (tx * tx + ty * ty) ** 0.5 or 1e-9

        def cl(L):
            f = min(1, L / tl)
            return tx * f, ty * f

        ox, oy = cl(l_out)
        ix, iy = cl(l_in)
        v.append([round(p[0], 2), round(p[1], 2)])
        o_.append([round(ox, 2), round(oy, 2)])
        i_.append([round(-ix, 2), round(-iy, 2)])
    return {"c": True, "v": v, "i": i_, "o": o_}


def _kmeans(rgb, alpha, k, iters=10):
    """Палитра k-means (k-means++ старт): надёжнее медианного сечения на мелких картинках."""
    px = rgb[alpha].astype(float)
    if len(px) > 6000:
        px = px[np.random.default_rng(1).choice(len(px), 6000, replace=False)]
    k = max(1, min(k, len(np.unique(px, axis=0))))
    rng = np.random.default_rng(7)
    cen = [px[rng.integers(len(px))]]
    for _ in range(k - 1):
        d = np.min([((px - c) ** 2).sum(1) for c in cen], axis=0)
        cen.append(px[rng.choice(len(px), p=d / d.sum())] if d.sum() > 0 else px[0])
    cen = np.array(cen)
    for _ in range(iters):
        lab = np.argmin(((px[:, None, :] - cen[None]) ** 2).sum(2), axis=1)
        for j in range(k):
            if (lab == j).any():
                cen[j] = px[lab == j].mean(0)
    full = rgb.reshape(-1, 3).astype(float)
    idx = np.argmin(((full[:, None, :] - cen[None]) ** 2).sum(2), axis=1).reshape(rgb.shape[:2])
    return idx, cen


def image_to_group(data: bytes, size=90, colors=8, grid=72, override=None, remove_bg=True):
    """Возвращает (группа Lottie, (ширина, высота)) — как svgimport.svg_to_group."""
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = im.size
    k = grid / max(w, h)
    nw, nh = max(2, round(w * k)), max(2, round(h * k))
    im = im.resize((nw, nh), Image.LANCZOS)
    arr = np.array(im)
    alpha = arr[..., 3] >= 128
    rgb = arr[..., :3]
    if alpha.all() and remove_bg:
        alpha = _remove_bg(rgb, alpha)
    if not alpha.any():
        raise ValueError("На картинке не осталось видимых пикселей")
    idx, centers = _kmeans(rgb, alpha, colors)
    pal = [int(round(v)) for c in centers for v in c]
    parts = []
    order = sorted(range(len(centers)), key=lambda c: -int(((idx == c) & alpha).sum()))
    for c in order:
        mask = (idx == c) & alpha
        if mask.sum() < max(3, 0.002 * nw * nh):
            continue
        shapes = []
        for lp in _loops(mask):
            if abs(_area(lp)) < 2:
                continue
            poly = _simplify_closed(lp, 0.7)
            if len(poly) >= 3:
                shapes.append({"ty": "sh", "nm": "trace", "ks": {"a": 0, "k": _bezier(poly)}})
        if not shapes:
            continue
        col = override or tuple(round(pal[c * 3 + j] / 255, 4) for j in range(3))
        parts.append({"ty": "gr", "nm": "badge part", "it": [
            *shapes,
            {"ty": "fl", "nm": "Fill", "r": 1, "o": {"a": 0, "k": 100}, "c": {"a": 0, "k": [*col, 1]}},
            {"ty": "st", "nm": "Seam", "o": {"a": 0, "k": 100}, "w": {"a": 0, "k": 0.8}, "lc": 2, "lj": 2,
             "c": {"a": 0, "k": [*col, 1]}},
            transform()]})
    if not parts:
        raise ValueError("Не удалось выделить фигуры — попробуй картинку покрупнее/контрастнее")
    s = size / max(nw, nh)
    tr = transform()
    tr["a"]["k"] = [nw / 2, nh / 2]
    tr["s"]["k"] = [round(s * 100, 3)] * 2
    return {"ty": "gr", "nm": "badge", "it": [*parts, tr]}, (nw * s, nh * s)
