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


def recolor(root, fill=None, stroke=None, skip=(), ip=0, op=60, cycles=1):
    """fill/stroke — спецификации из colors.parse_spec (один цвет, смена, перелив).
    Сплошные заливки/обводки красятся точно или анимируются, градиенты —
    с сохранением светотени (при смене цветов тоже). Узлы из skip не трогаются."""
    cnt = [0]

    def apply(node, spec, solid, grad):
        idx = cnt[0]
        cnt[0] += 1
        sp = C.phased(spec, idx)
        if node["ty"] == solid:
            node["c"] = C.color_prop(sp, ip, op, cycles)
        elif sp["mode"] == "static":
            C.tint_gradient(node.get("g", {}), sp["colors"][0])
        else:
            C.animate_gradient(node.get("g", {}), sp["colors"], ip, op, cycles)

    def rec(node):
        if isinstance(node, dict):
            if matches(node, skip):
                return
            ty = node.get("ty")
            if fill and ty in ("fl", "gf"):
                apply(node, fill, "fl", "gf")
            elif stroke and ty in ("st", "gs"):
                apply(node, stroke, "st", "gs")
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
