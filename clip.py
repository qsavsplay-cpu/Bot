"""Обрезка контуров по холсту: по правилам Telegram объекты TGS не должны выходить за холст."""
import numpy as np


def _flatten(sh, steps=8):
    v, i, o = sh["v"], sh.get("i", []), sh.get("o", [])
    n = len(v)
    pts = []
    segs = n if sh.get("c", True) else n - 1
    for k in range(segs):
        k2 = (k + 1) % n
        p0 = v[k]
        p1 = (v[k][0] + o[k][0], v[k][1] + o[k][1])
        p2 = (v[k2][0] + i[k2][0], v[k2][1] + i[k2][1])
        p3 = v[k2]
        straight = p1 == tuple(p0) and p2 == tuple(p3)
        for t in range(1 if straight else steps):
            u = t / (1 if straight else steps)
            a, b, c, d = (1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u * u, u ** 3
            pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def _clip(poly, clip):
    """Sutherland–Hodgman: многоугольник poly по выпуклому clip (против часовой)."""
    out = poly
    for k in range(len(clip)):
        a, b = clip[k], clip[(k + 1) % len(clip)]
        inp, out = out, []
        if not inp:
            break

        def inside(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0

        def cross(p, q):
            dx1, dy1, dx2, dy2 = q[0] - p[0], q[1] - p[1], b[0] - a[0], b[1] - a[1]
            den = dx1 * dy2 - dy1 * dx2
            t = ((a[0] - p[0]) * dy2 - (a[1] - p[1]) * dx2) / den if den else 0
            return (p[0] + t * dx1, p[1] + t * dy1)

        for j in range(len(inp)):
            cur, prev = inp[j], inp[j - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
    return out


def clip_shapes(shapes, M, w, h, inset=0.5):
    """shapes (локальные координаты) -> копия, где всё лежит внутри холста w×h.
    M — матрица «локальные -> холст». Целиком внутри: без изменений; целиком снаружи: убирается."""
    try:
        Minv = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        return shapes
    corners = [(inset, inset), (w - inset, inset), (w - inset, h - inset), (inset, h - inset)]
    clip = [tuple((Minv @ np.array([x, y, 1.0]))[:2]) for x, y in corners]
    area = sum(clip[k][0] * clip[(k + 1) % 4][1] - clip[(k + 1) % 4][0] * clip[k][1] for k in range(4))
    if area < 0:
        clip = clip[::-1]
    xs, ys = [p[0] for p in clip], [p[1] for p in clip]
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    out = []
    for sh in shapes:
        if sh.get("ty") != "sh":
            out.append(sh)
            continue
        bez = sh["ks"]["k"]
        vx, vy = [p[0] for p in bez["v"]], [p[1] for p in bez["v"]]
        if min(vx) >= bx0 and max(vx) <= bx1 and min(vy) >= by0 and max(vy) <= by1:
            out.append(sh)
            continue
        if max(vx) < bx0 or min(vx) > bx1 or max(vy) < by0 or min(vy) > by1:
            continue
        poly = _clip(_flatten(bez), clip)
        if len(poly) < 3:
            continue
        out.append({"ty": "sh", "nm": "glyph", "ks": {"a": 0, "k": {
            "c": True, "v": [[round(x, 2), round(y, 2)] for x, y in poly],
            "i": [[0, 0]] * len(poly), "o": [[0, 0]] * len(poly)}}})
    return out
