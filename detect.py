"""Автопоиск текстовой папки/слоя: по имени (надпись, text, trst, HELL…) и по «форме» (ряд букв)."""
import statistics

import lottie_ops as O

MARK = "__TEXT__"
# имя -> вес (точное совпадение = вес, вхождение = 0.7 веса)
NAME_WEIGHTS = {
    "надпись": 100, "текст": 95, "text": 95, "txt": 90, "tekst": 90, "trst": 90,
    "nick": 85, "nickname": 85, "hell": 80, "letters": 70, "title": 60, "label": 60,
    "name": 55, "word": 50, "emoji": 45, "лого": 50, "logo": 50,
}


def name_score(nm):
    nm = str(nm or "").strip().lower()
    if not nm:
        return 0
    if nm in NAME_WEIGHTS:
        return NAME_WEIGHTS[nm]
    best = 0
    for k, w in NAME_WEIGHTS.items():
        if k in nm:
            best = max(best, int(w * 0.7))
    return best


def items_of(node):
    return node.get("it", []) if node.get("ty") == "gr" else node.get("shapes", [])


def _tr(g):
    return next((i for i in g.get("it", []) if i.get("ty") == "tr"), None)


def box_of(node):
    """Границы содержимого node в его собственных координатах (с учётом трансформов вложенных папок)."""
    boxes = []
    for it in items_of(node):
        ty = it.get("ty")
        if ty == "sh":
            sh = O.shape_of(it)
            if sh:
                xs, ys = [p[0] for p in sh["v"]], [p[1] for p in sh["v"]]
                boxes.append((min(xs), min(ys), max(xs), max(ys)))
        elif ty == "gr":
            b = box_of(it)
            if b:
                tr = _tr(it)
                boxes.append(O.to_parent(tr, b) if tr else b)
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _glyph_boxes(node):
    boxes = []
    for it in items_of(node):
        ty = it.get("ty")
        if ty == "gr":
            b = box_of(it)
            if b:
                tr = _tr(it)
                boxes.append(O.to_parent(tr, b) if tr else b)
        elif ty == "sh":
            sh = O.shape_of(it)
            if sh:
                xs, ys = [p[0] for p in sh["v"]], [p[1] for p in sh["v"]]
                boxes.append((min(xs), min(ys), max(xs), max(ys)))
    boxes = [b for b in boxes if b[2] - b[0] > 1e-3 and b[3] - b[1] > 1e-3]
    boxes.sort(key=lambda b: (b[0] + b[2]) / 2)
    merged = []
    for b in boxes:                       # склеиваем «дырки» букв (o, a, e…) с их внешним контуром
        if merged:
            m = merged[-1]
            ov = min(m[2], b[2]) - max(m[0], b[0])
            if ov > 0.5 * min(m[2] - m[0], b[2] - b[0]):
                merged[-1] = (min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3]))
                continue
        merged.append(b)
    return merged


def struct_score(node, canvas=(512, 512)):
    """Похоже ли содержимое на строку из ≥3 букв? Возвращает 0 или оценку."""
    boxes = _glyph_boxes(node)
    n = len(boxes)
    if n < 3:
        return 0, ""
    W = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    H = max(b[3] for b in boxes) - min(b[1] for b in boxes)
    if H <= 0:
        return 0, ""
    hs = [b[3] - b[1] for b in boxes]
    med = statistics.median(hs)
    good = sum(1 for h in hs if 0.35 * med <= h <= 2.2 * med)
    asp = W / H
    if good / n < 0.7 or asp < 1.5 or W * H > 0.35 * canvas[0] * canvas[1]:
        return 0, ""
    return 25 + 4 * min(n, 12) + min(asp, 8) * 3, f"{n} букв(ы), {round(W)}×{round(H)}"


def find_candidates(data, limit=6):
    """Список кандидатов по убыванию оценки: {node, parent, name, score, bbox, path, why}."""
    canvas = (data.get("w", 512), data.get("h", 512))
    out = []

    def visit(node, parent, path):
        if isinstance(node, dict):
            ty = node.get("ty")
            here = path
            if ty in ("gr", 4, 5) and "nm" in node or ty in ("gr", 4, 5):
                nm = node.get("nm")
                here = path + [str(nm)]
                ns = 100 if ty == 5 else name_score(nm)
                ss, why = struct_score(node, canvas) if ty in ("gr", 4) else (0, "")
                total = ns + ss
                if total >= 40:
                    reasons = []
                    if ns:
                        reasons.append("текстовый слой" if ty == 5 else f"имя «{nm}»")
                    if why:
                        reasons.append(why)
                    out.append({"node": node, "parent": parent, "name": nm, "score": total,
                                "bbox": box_of(node), "path": " › ".join(here[-3:]),
                                "why": ", ".join(reasons)})
            for v in node.values():
                visit(v, None, here)
        elif isinstance(node, list):
            for v in node:
                visit(v, node, path)

    visit(data, None, [])
    out.sort(key=lambda c: -c["score"])
    return out[:limit]


def resolve_text(data, layer):
    """Возвращает имя, по которому replace_text найдёт нужный узел.
    layer — имя, список через запятую или 'auto'. Неоднозначные/ненайденные имена -> автопоиск,
    найденный узел помечается уникальным именем MARK."""
    names = [x.strip() for x in str(layer or "auto").split(",") if x.strip() and x.strip().lower() != "auto"]
    if names:
        hits = []
        O.find_nodes(data, names, hits)
        if len(hits) == 1:
            return names[0]
        if len(hits) > 1:                       # одинаковые имена («Group») — берём самый «текстовый»
            cands = {id(c["node"]): c for c in find_candidates(data, 50)}
            best = max(hits, key=lambda h: cands.get(id(h[0]), {"score": 0})["score"])
            best[0]["nm"] = MARK
            return MARK
    c = find_candidates(data, 1)
    if c:
        c[0]["node"]["nm"] = MARK
        return MARK
    return names[0] if names else "auto"


def set_text_node(data, cand):
    """Подготовка выбранного кандидата (пока ничего не меняет, оставлено для расширения)."""
    return cand["node"]


def bake_node(data, cand):
    """Возвращает имя, по которому узел находится однозначно; безымянные/дублирующиеся помечает MARK."""
    node, nm = cand["node"], cand["name"]
    if nm:
        hits = []
        O.find_nodes(data, [nm], hits)
        if len(hits) == 1 and hits[0][0] is node:
            return nm
    old = []
    O.find_nodes(data, [MARK], old)
    for n, _ in old:
        if n is not node:
            n["nm"] = "text (old)"
    node["nm"] = MARK
    return MARK


def bake(data):
    """Для нового шаблона: лучший кандидат -> устойчивое имя слоя ('auto' если ничего не нашли)."""
    c = find_candidates(data, 1)
    return bake_node(data, c[0]) if c else "auto"
