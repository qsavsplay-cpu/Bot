"""Поиск, удаление, перекраска, структура Lottie."""
import fnmatch

import colors as C

LAYER_TYPES = (0, 1, 2, 3, 4, 5, "gr")
TYPE_NAMES = {0: "precomp", 1: "solid", 2: "image", 3: "null", 4: "shape", 5: "text", "gr": "папка"}


def matches(node, patterns):
    if not patterns or not isinstance(node, dict) or node.get("ty") not in LAYER_TYPES:
        return False
    nm = str(node.get("nm", "")).lower()
    return any(fnmatch.fnmatchcase(nm, p.lower()) for p in patterns)


def find_nodes(node, patterns, out, parent=None):
    """Собирает (узел, список-родитель) для слоёв/папок, чьё имя подходит под patterns."""
    if isinstance(node, dict):
        if matches(node, patterns):
            out.append((node, parent))
        for v in node.values():
            find_nodes(v, patterns, out, None)
    elif isinstance(node, list):
        for v in node:
            find_nodes(v, patterns, out, node)


def contains(node, target):
    """Есть ли target внутри node (по идентичности объекта)."""
    if node is target:
        return True
    if isinstance(node, dict):
        return any(contains(v, target) for v in node.values())
    if isinstance(node, list):
        return any(contains(v, target) for v in node)
    return False


def remove_nodes(root, patterns, protect=(), keep_nodes=()):
    """Удаляет слои/папки по маскам. Не трогает protect (по имени) и всё, что содержит keep_nodes."""
    found = []
    find_nodes(root, patterns, found)
    n = 0
    for node, parent in found:
        if parent is None or matches(node, protect):
            continue
        if any(contains(node, k) for k in keep_nodes):
            continue
        for i, x in enumerate(parent):
            if x is node:
                del parent[i]
                n += 1
                break
    return n


def _base_color(prop):
    k = prop.get("k") if isinstance(prop, dict) else None
    if isinstance(prop, dict) and prop.get("a") == 1 and isinstance(k, list) and k and isinstance(k[0], dict):
        k = k[0].get("s")
    return list(k[:3]) if isinstance(k, list) and len(k) >= 3 and isinstance(k[0], (int, float)) else [0, 0, 0]


def _lightness_stats(root, skip, kinds):
    """Средняя яркость цветов и доля «нейтральных» (серых/чёрных/белых) — для умной перекраски."""
    ls, neutral = [], 0

    def add(col):
        nonlocal neutral
        h, l, s = C.colorsys.rgb_to_hls(*col)
        if l > 0.9 and s < 0.12:
            return                                  # белые блики не учитываем
        ls.append(l)
        neutral += s < 0.12

    def rec(n):
        if isinstance(n, dict):
            if matches(n, skip):
                return
            ty = n.get("ty")
            if ty == kinds[0]:
                add(_base_color(n.get("c")))
            elif ty == kinds[1]:
                for st in C.gradient_stops(n.get("g", {})):
                    add(st)
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)

    rec(root)
    return (sum(ls) / len(ls) if ls else 0.5), (neutral / len(ls) if ls else 0)


def recolor(root, fill=None, stroke=None, skip=(), ip=0, op=60, cycles=1):
    """fill/stroke — спецификации (один цвет, смена, перелив).
    Заливки: «умная» перекраска с сохранением светотени (тени/блики остаются), белые блики не трогаем,
    если эмодзи почти целиком тёмное/серое — красим и чёрное.
    Контуры: ровный цвет. Градиенты и анимация цвета поддерживаются. Узлы из skip не трогаются."""
    mf, nf = _lightness_stats(root, skip, ("fl", "gf"))
    ms, _ = _lightness_stats(root, skip, ("st", "gs"))
    keep_dark = nf < 0.6
    cnt = [0]

    def paint_fill(node, spec):
        sp = C.phased(spec, cnt[0])
        if node["ty"] == "fl":
            base = _base_color(node.get("c"))
            cols = [C.remap(base, t, mf, keep_dark) for t in sp["colors"]]
            node["c"] = C.color_prop({"mode": "static" if len(cols) == 1 else "cycle", "colors": cols}, ip, op, cycles)
        elif len(sp["colors"]) == 1:
            C.retint_gradient(node.get("g", {}), sp["colors"][0], mf, keep_dark)
        else:
            C.animate_gradient(node.get("g", {}), sp["colors"], ip, op, cycles, mf, keep_dark)

    def paint_stroke(node, spec):
        sp = C.phased(spec, cnt[0])
        if node["ty"] == "st":
            node["c"] = C.color_prop(sp, ip, op, cycles)
        elif len(sp["colors"]) == 1:
            C.retint_gradient(node.get("g", {}), sp["colors"][0], ms, False)
        else:
            C.animate_gradient(node.get("g", {}), sp["colors"], ip, op, cycles, ms, False)

    def rec(node):
        if isinstance(node, dict):
            if matches(node, skip):
                return
            ty = node.get("ty")
            if fill and ty in ("fl", "gf"):
                paint_fill(node, fill); cnt[0] += 1
            elif stroke and ty in ("st", "gs"):
                paint_stroke(node, stroke); cnt[0] += 1
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for v in node:
                rec(v)

    rec(root)
    return cnt[0]


def tree(root, max_depth=5):
    lines = []

    def shapes(items, depth):
        for it in items:
            if isinstance(it, dict) and it.get("ty") == "gr":
                lines.append("  " * depth + f"📁 {it.get('nm')}")
                if depth < max_depth:
                    shapes(it.get("it", []), depth + 1)

    def layers(ls, depth):
        for l in ls:
            lines.append("  " * depth + f"▪ [{TYPE_NAMES.get(l.get('ty'), l.get('ty'))}] {l.get('nm')}")
            shapes(l.get("shapes", []), depth + 1)

    layers(root.get("layers", []), 0)
    for a in root.get("assets", []):
        if "layers" in a:
            lines.append(f"\n== precomp {a.get('id')} ==")
            layers(a["layers"], 0)
    return "\n".join(lines)


def static_val(prop, default):
    if not isinstance(prop, dict):
        return default
    k = prop.get("k")
    if prop.get("a") == 1 and isinstance(k, list) and k and isinstance(k[0], dict):
        s = k[0].get("s")
        return s if s is not None else default
    return k if k is not None else default


def to_parent(tr, bbox):
    """bbox внутри папки -> bbox в координатах её родителя (по трансформу папки)."""
    p = static_val(tr.get("p"), [0, 0]); a = static_val(tr.get("a"), [0, 0])
    s = static_val(tr.get("s"), [100, 100])

    def f(x, y):
        return (p[0] + (x - a[0]) * s[0] / 100, p[1] + (y - a[1]) * s[1] / 100)

    (x0, y0), (x1, y1) = f(bbox[0], bbox[1]), f(bbox[2], bbox[3])
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def shape_of(sh):
    """Безье-контур пути (для анимированного — из первого ключа) или None."""
    ks = sh.get("ks", {})
    k = ks.get("k")
    if ks.get("a") == 1 and isinstance(k, list) and k and isinstance(k[0], dict):
        s = k[0].get("s")
        k = s[0] if isinstance(s, list) and s else s
    return k if isinstance(k, dict) and k.get("v") else None


def path_bbox(node):
    """Границы всех путей внутри node (анимированные — по первому ключу) или None."""
    pts = []

    def rec(n):
        if isinstance(n, dict):
            if n.get("ty") == "sh":
                sh = shape_of(n)
                if sh:
                    pts.extend(sh["v"])
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)

    rec(node)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def find_nodes_list(root, patterns):
    out = []
    find_nodes(root, patterns, out)
    return out
