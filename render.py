"""Мини-рендер Lottie на Pillow+numpy: превью шаблонов (кадры/GIF). Чистый Python, без внешних библиотек."""
import math

import numpy as np
from PIL import Image, ImageDraw

STYLES = ("fl", "st", "gf", "gs")
GEOM = ("sh", "rc", "el")


# ------------------------------------------------------------ значения/анимация
def _ease(x1, y1, x2, y2, t):
    lo, hi = 0.0, 1.0
    for _ in range(16):
        u = (lo + hi) / 2
        x = 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
        lo, hi = (u, hi) if x < t else (lo, u)
    u = (lo + hi) / 2
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3


def _lerp(a, b, t):
    if isinstance(a, dict):
        return {"v": _lerp(a["v"], b["v"], t), "i": _lerp(a["i"], b["i"], t),
                "o": _lerp(a["o"], b["o"], t), "c": a.get("c", True)} if len(a.get("v", [])) == len(b.get("v", [])) else a
    if isinstance(a, (list, tuple)):
        return [_lerp(x, y, t) for x, y in zip(a, b)] if len(a) == len(b) else a
    return a + (b - a) * t


def _first(x):
    return x[0] if isinstance(x, (list, tuple)) else x


def _s(kf):
    s = kf.get("s")
    return s[0] if isinstance(s, list) and s and isinstance(s[0], dict) else s


def val(prop, f, default=0):
    if not isinstance(prop, dict):
        return default
    k = prop.get("k")
    if prop.get("a") != 1 or not (isinstance(k, list) and k and isinstance(k[0], dict) and "t" in k[0]):
        return k if k is not None else default
    if f <= k[0]["t"]:
        return _s(k[0])
    for i in range(len(k) - 1):
        a, b = k[i], k[i + 1]
        if a["t"] <= f < b["t"]:
            s = _s(a)
            e = a.get("e")
            e = (e[0] if isinstance(e, list) and e and isinstance(e[0], dict) else e) if e is not None else _s(b)
            if a.get("h") or s is None or e is None:
                return s
            t = (f - a["t"]) / max(1e-9, b["t"] - a["t"])
            o, i_ = a.get("o"), a.get("i")
            if isinstance(o, dict) and isinstance(i_, dict):
                t = _ease(_first(o["x"]), _first(o["y"]), _first(i_["x"]), _first(i_["y"]), t)
            return _lerp(s, e, t)
    return _s(k[-1])


# ------------------------------------------------------------ геометрия
def _T(x, y): return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], float)
def _Sc(x, y): return np.array([[x, 0, 0], [0, y, 0], [0, 0, 1]], float)
def _Rot(d):
    c, s = math.cos(math.radians(d)), math.sin(math.radians(d))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


def num(prop, f, d=0):
    v = val(prop, f, d)
    while isinstance(v, (list, tuple)):
        v = v[0] if v else d
    return float(v)


def tmat(ks, f):
    a = val(ks.get("a"), f, [0, 0, 0])
    pp = ks.get("p")
    p = [val(pp["x"], f, 0), val(pp["y"], f, 0)] if isinstance(pp, dict) and pp.get("s") else val(pp, f, [0, 0, 0])
    s = val(ks.get("s"), f, [100, 100, 100])
    r = num(ks.get("r"), f, 0) if "r" in ks else num(ks.get("rz"), f, 0)
    return _T(p[0], p[1]) @ _Rot(r) @ _Sc(s[0] / 100, s[1] / 100) @ _T(-a[0], -a[1])


def _pt(M, x, y):
    return M[0, 0] * x + M[0, 1] * y + M[0, 2], M[1, 0] * x + M[1, 1] * y + M[1, 2]


def flatten(sh, M):
    """Безье -> список точек (устройство), closed."""
    v, i, o = sh["v"], sh.get("i", []), sh.get("o", [])
    n = len(v)
    if n == 0:
        return [], False
    closed = sh.get("c", True)
    pts = []
    segs = n if closed else n - 1
    for k in range(segs):
        k2 = (k + 1) % n
        p0 = _pt(M, *v[k])
        p1 = _pt(M, v[k][0] + o[k][0], v[k][1] + o[k][1])
        p2 = _pt(M, v[k2][0] + i[k2][0], v[k2][1] + i[k2][1])
        p3 = _pt(M, *v[k2])
        L = abs(p1[0] - p0[0]) + abs(p1[1] - p0[1]) + abs(p2[0] - p1[0]) + abs(p2[1] - p1[1]) + abs(p3[0] - p2[0]) + abs(p3[1] - p2[1])
        if L < 1e-6 or (p1 == p0 and p2 == p3):
            pts.append(p0)
            continue
        steps = int(min(24, max(3, L / 6)))
        for t in range(steps):
            u = t / steps
            a_, b_, c_, d_ = (1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u * u, u ** 3
            pts.append((a_ * p0[0] + b_ * p1[0] + c_ * p2[0] + d_ * p3[0], a_ * p0[1] + b_ * p1[1] + c_ * p2[1] + d_ * p3[1]))
    if not closed:
        pts.append(_pt(M, *v[-1]))
    return pts, closed


def _area(p):
    return 0.5 * sum(p[k][0] * p[(k + 1) % len(p)][1] - p[(k + 1) % len(p)][0] * p[k][1] for k in range(len(p)))


def poly_mask(polys, W, H, evenodd=False):
    """Маска заливки составного контура (nonzero/evenodd)."""
    acc = np.zeros((H, W), np.int16)
    for pts in polys:
        if len(pts) < 3:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, y0 = max(0, int(math.floor(min(xs))) - 1), max(0, int(math.floor(min(ys))) - 1)
        x1, y1 = min(W, int(math.ceil(max(xs))) + 2), min(H, int(math.ceil(max(ys))) + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        img = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(img).polygon([(x - x0, y - y0) for x, y in pts], fill=1)
        a = np.asarray(img, np.int16)
        acc[y0:y1, x0:x1] += a if evenodd else (a if _area(pts) > 0 else -a)
    return (acc % 2 != 0) if evenodd else (acc != 0)


# ------------------------------------------------------------ рисование
def _over(dst, src):
    if src is None:
        return dst
    if dst is None:
        return src
    return src + dst * (1 - src[..., 3:4])


def _color(c, f):
    v = val(c, f, [0, 0, 0, 1])
    v = list(v) + [1] * (4 - len(v))
    return np.array(v[:3], np.float32)


def _gradient(it, f, M, W, H):
    g = it.get("g", {})
    p = g.get("p", 0)
    arr = val(g.get("k"), f, [])
    if not p or len(arr) < p * 4:
        return np.zeros((H, W, 3), np.float32)
    pos = [arr[i * 4] for i in range(p)]
    cols = np.array([arr[i * 4 + 1:i * 4 + 4] for i in range(p)], np.float32)
    s, e = val(it.get("s"), f, [0, 0]), val(it.get("e"), f, [100, 0])
    (sx, sy), (ex, ey) = _pt(M, *s[:2]), _pt(M, *e[:2])
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    if it.get("t") == 2:
        d = np.sqrt((xx - sx) ** 2 + (yy - sy) ** 2) / max(1e-6, math.hypot(ex - sx, ey - sy))
    else:
        dx, dy = ex - sx, ey - sy
        d = ((xx - sx) * dx + (yy - sy) * dy) / max(1e-6, dx * dx + dy * dy)
    d = np.clip(d, 0, 1)
    return np.stack([np.interp(d, pos, cols[:, c]) for c in range(3)], -1).astype(np.float32)


def _paint(mask, style, f, M, op, W, H):
    ty = style["ty"]
    a = num(style.get("o"), f, 100) / 100 * op
    if a <= 0 or not mask.any():
        return None
    if ty in ("fl", "st"):
        rgb = np.broadcast_to(_color(style.get("c"), f), (H, W, 3))
    else:
        rgb = _gradient(style, f, M, W, H)
    al = mask.astype(np.float32)[..., None] * a
    return np.concatenate([rgb * al, al], -1)


def _paths_of(items_geom, f, M):
    out = []
    for it in items_geom:
        ty = it["ty"]
        if ty == "sh":
            sh = val(it.get("ks"), f, None)
            if isinstance(sh, dict) and sh.get("v"):
                out.append(flatten(sh, M))
        elif ty == "rc":
            p, s = val(it.get("p"), f, [0, 0]), val(it.get("s"), f, [0, 0])
            x0, y0, x1, y1 = p[0] - s[0] / 2, p[1] - s[1] / 2, p[0] + s[0] / 2, p[1] + s[1] / 2
            out.append(([_pt(M, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))], True))
        elif ty == "el":
            p, s = val(it.get("p"), f, [0, 0]), val(it.get("s"), f, [0, 0])
            out.append(([_pt(M, p[0] + s[0] / 2 * math.cos(t * math.pi / 20), p[1] + s[1] / 2 * math.sin(t * math.pi / 20)) for t in range(40)], True))
    return out


def draw_items(items, f, M, op, W, H):
    out = None
    for idx in range(len(items) - 1, -1, -1):
        it = items[idx]
        ty = it.get("ty")
        if it.get("hd"):
            continue
        if ty == "gr":
            sub = it.get("it", [])
            tr = next((x for x in sub if x.get("ty") == "tr"), None)
            M2, op2 = M, op
            if tr:
                M2 = M @ tmat(tr, f)
                op2 = op * num(tr.get("o"), f, 100) / 100
            out = _over(out, draw_items(sub, f, M2, op2, W, H))
        elif ty in STYLES:
            geom = [x for x in items[:idx] if x.get("ty") in GEOM and not x.get("hd")]
            paths = _paths_of(geom, f, M)
            if not paths:
                continue
            if ty in ("fl", "gf"):
                mask = poly_mask([p for p, _ in paths], W, H, it.get("r") == 2)
            else:
                w = max(0.5, num(it.get("w"), f, 1) * math.sqrt(abs(np.linalg.det(M[:2, :2]))))
                img = Image.new("L", (W, H), 0)
                d = ImageDraw.Draw(img)
                for pts, closed in paths:
                    if len(pts) > 1:
                        d.line(pts + ([pts[0]] if closed else []), fill=255, width=max(1, round(w)), joint="curve")
                mask = np.asarray(img) > 127
            out = _over(out, _paint(mask, it, f, M, op, W, H))
    return out


def layer_mask(layer, f, M, W, H):
    total = None
    for m in layer.get("masksProperties", []):
        mode = m.get("mode", "a")
        if mode == "n":
            continue
        sh = val(m.get("pt"), f, None)
        if not (isinstance(sh, dict) and sh.get("v")):
            continue
        ma = poly_mask([flatten(sh, M)[0]], W, H).astype(np.float32) * num(m.get("o"), f, 100) / 100
        if m.get("inv"):
            ma = 1 - ma
        if mode == "a":
            total = ma if total is None else np.maximum(total, ma)
        elif mode == "s":
            total = (1 - ma) if total is None else total * (1 - ma)
        elif mode == "i":
            total = ma if total is None else total * ma
    return total


def render_comp(layers, f, base, assets, W, H, depth=0):
    by_ind = {l["ind"]: l for l in layers if "ind" in l}
    cache = {}

    def mat(l):
        key = id(l)
        if key not in cache:
            m = tmat(l.get("ks", {}), f)
            par = by_ind.get(l.get("parent"))
            cache[key] = (mat(par) if par is not None and par is not l else base) @ m
        return cache[key]

    def own_img(l, ff):
        if l.get("hd") or not (l.get("ip", -1e9) <= ff < l.get("op", 1e9)) or l.get("ty") == 3:
            return None
        ks = l.get("ks", {})
        M = mat(l)
        op = num(ks.get("o"), ff, 100) / 100
        if op <= 0:
            return None
        ty = l.get("ty")
        img = None
        if ty == 4:
            img = draw_items(l.get("shapes", []), ff, M, op, W, H)
        elif ty == 1:
            c = l.get("sc", "#000000").lstrip("#")
            rgb = np.array([int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)
            pts = [_pt(M, x, y) for x, y in ((0, 0), (l.get("sw", 0), 0), (l.get("sw", 0), l.get("sh", 0)), (0, l.get("sh", 0)))]
            al = poly_mask([pts], W, H).astype(np.float32)[..., None] * op
            img = np.concatenate([np.broadcast_to(rgb, (H, W, 3)) * al, al], -1)
        elif ty == 0 and depth < 12:
            asset = assets.get(l.get("refId"))
            if asset:
                img = render_comp(asset.get("layers", []), ff - l.get("st", 0), M, assets, W, H, depth + 1)
                if img is not None:
                    img = img * op
        if img is None:
            return None
        if l.get("hasMask") or l.get("masksProperties"):
            mk = layer_mask(l, ff, M, W, H)
            if mk is not None:
                img = img * mk[..., None]
        return img

    out = None
    for idx in range(len(layers) - 1, -1, -1):
        l = layers[idx]
        if l.get("td"):
            continue
        img = own_img(l, f)
        if img is None:
            continue
        tt = l.get("tt")
        if tt and idx > 0 and layers[idx - 1].get("td"):
            mt = own_img({**layers[idx - 1], "td": 0}, f)
            if mt is None:
                continue
            a = mt[..., 3:4]
            if tt in (3, 4):
                lum = (0.3 * mt[..., 0:1] + 0.59 * mt[..., 1:2] + 0.11 * mt[..., 2:3])
                a = lum
            img = img * ((1 - a) if tt in (2, 4) else a)
        out = _over(out, img)
    return out


def render_frame(data, frame, size=200, bg=None, ss=2):
    """Один кадр -> PIL RGBA. bg=(r,g,b) — подложка, иначе прозрачный."""
    cw, ch = data.get("w", 512), data.get("h", 512)
    W = H = size * ss
    base = _Sc(W / cw, H / ch)
    assets = {a["id"]: a for a in data.get("assets", []) if "id" in a}
    img = render_comp(data.get("layers", []), frame, base, assets, W, H)
    if img is None:
        img = np.zeros((H, W, 4), np.float32)
    a = np.clip(img[..., 3:4], 1e-6, 1)
    rgb = np.clip(img[..., :3] / a, 0, 1)
    out = Image.fromarray((np.concatenate([rgb, np.clip(img[..., 3:4], 0, 1)], -1) * 255).astype(np.uint8), "RGBA")
    out = out.resize((size, size), Image.LANCZOS)
    if bg is not None:
        base_img = Image.new("RGBA", (size, size), (*bg, 255))
        base_img.alpha_composite(out)
        return base_img
    return out


def frames_for(data, n=18, size=160):
    ip, op = data.get("ip", 0), data.get("op", 60)
    span = max(1, op - ip)
    return [render_frame(data, ip + span * i / n, size) for i in range(n)]


def node_scale(data, node, frame=None):
    """Во сколько раз единицы внутри node крупнее/мельче пикселей холста (по статичным трансформам)."""
    f = data.get("ip", 0) if frame is None else frame
    assets = {a["id"]: a for a in data.get("assets", []) if "id" in a}
    base = np.eye(3)

    def tr_of(items):
        return next((x for x in items if isinstance(x, dict) and x.get("ty") == "tr"), None)

    def in_items(items, M):
        for it in items:
            if it is node:
                return M
            if isinstance(it, dict) and it.get("ty") == "gr":
                sub = it.get("it", [])
                tr = tr_of(sub)
                r = in_items(sub, M @ tmat(tr, f) if tr else M)
                if r is not None:
                    return r
        return None

    def in_layers(layers, M, depth=0):
        by = {l["ind"]: l for l in layers if "ind" in l}

        def mat(l, guard=0):
            m = tmat(l.get("ks", {}), f)
            par = by.get(l.get("parent"))
            return (mat(par, guard + 1) if par is not None and guard < 20 else M) @ m

        for l in layers:
            Ml = mat(l)
            if l is node:
                return Ml
            r = in_items(l.get("shapes", []), Ml) if l.get("ty") == 4 else None
            if r is None and l.get("ty") == 0 and depth < 12 and l.get("refId") in assets:
                r = in_layers(assets[l["refId"]].get("layers", []), Ml, depth + 1)
            if r is not None:
                return r
        return None

    M = in_layers(data.get("layers", []), base)
    if M is None:
        return 1.0
    return float(math.sqrt(abs(np.linalg.det(M[:2, :2]))) or 1.0)
