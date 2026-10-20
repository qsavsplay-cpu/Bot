"""Сборка результата: удаление → перекраска → текст → стиль текста → лого."""
import copy

import colors as C
import detect as D
import lottie_ops as O
import lottie_text as T
import render as R
import svgimport as S
import trace as TR

DEFAULTS = dict(
    layer="auto", size=80, width=None, font=None,
    remove=[], autologo=True, skip=[], fill=None, stroke=None,
    text_fill=None, text_stroke=None, stroke_w=None, cycles=1,
    logo_size=90, logo_pos="left", logo_dx=0, logo_dy=0, logo_color=None, logo_slot="LOGO",
    img_colors=8, img_grid=72, img_bg=True, mode="text", logo_scale=100,
)


def _static_rgb(spec):
    return spec["colors"][0] if spec and spec["mode"] == "static" else None


def _num(prop, d):
    v = O.static_val(prop, d)
    return v[0] if isinstance(v, list) else v


def _style_text(r, s, ip, op):
    node = r["container"]
    it = node["it"]
    shapes = [i for i in it if i.get("ty") == "sh"]
    fills = [i for i in it if i.get("ty") in ("fl", "gf")]
    strokes = [i for i in it if i.get("ty") in ("st", "gs")]
    tr = next(i for i in it if i.get("ty") == "tr")
    bbox = r["bbox"] or (-50, -20, 50, 20)
    width = s["stroke_w"] or (_num(strokes[0].get("w"), 3) if strokes else 3)

    spec = s["text_fill"]
    if spec:
        if spec["mode"] == "none":
            fills = []
        elif spec["mode"] == "grad":
            fills = [C.grad_item(spec, ip, op, s["cycles"], bbox, nm="Text Fill")]
        else:
            fills = [{"ty": "fl", "nm": "Text Fill", "r": 1, "o": {"a": 0, "k": 100},
                      "c": C.color_prop(spec, ip, op, s["cycles"])}]
    spec = s["text_stroke"]
    if spec:
        if spec["mode"] == "none":
            strokes = []
        elif spec["mode"] == "grad":
            strokes = [C.grad_item(spec, ip, op, s["cycles"], bbox, stroke_w=width, nm="Text Stroke")]
        else:
            strokes = [{"ty": "st", "nm": "Text Stroke", "o": {"a": 0, "k": 100}, "lj": 1, "lc": 1,
                        "w": {"a": 0, "k": width}, "c": C.color_prop(spec, ip, op, s["cycles"])}]
    elif s["stroke_w"] and strokes:
        for st in strokes:
            st["w"] = {"a": 0, "k": width}
    node["it"] = [*shapes, *fills, *strokes, tr]


def _logo_group(logo, s, size=None):
    """logo = {"kind": "svg"|"img", "data": bytes}"""
    size = size or s["logo_size"]
    ov = _static_rgb(s["logo_color"])
    if logo["kind"] == "svg":
        return S.svg_to_group(logo["data"], size, ov)
    return TR.image_to_group(logo["data"], size, s["img_colors"], s["img_grid"], ov, s["img_bg"])


def _logo_into_slot(data, s, svg):
    found = []
    O.find_nodes(data, [s["logo_slot"]], found)
    n = 0
    for node, _ in found:
        if node.get("ty") != "gr":
            continue
        bb = O.path_bbox(node.get("it", []))
        dim = max(bb[2] - bb[0], bb[3] - bb[1]) if bb else s["logo_size"]
        grp, _ = _logo_group(svg, s, dim or s["logo_size"])
        if bb:
            grp["it"][-1]["p"]["k"] = [round((bb[0] + bb[2]) / 2, 2), round((bb[1] + bb[3]) / 2, 2)]
        tr = next((i for i in node.get("it", []) if i.get("ty") == "tr"), T.transform())
        node["it"] = [grp, tr]
        n += 1
    return n


def _logo_beside(r, s, svg):
    if r["parent"] is None:
        return False
    grp, (lw, lh) = _logo_group(svg, s)
    tr = next(i for i in r["container"]["it"] if i.get("ty") == "tr")
    x0, y0, x1, y1 = O.to_parent(tr, r["bbox"])
    cx, cy, gap, pos = (x0 + x1) / 2, (y0 + y1) / 2, 10, s["logo_pos"]
    x, y = {"left": (x0 - gap - lw / 2, cy), "right": (x1 + gap + lw / 2, cy),
            "above": (cx, y0 - gap - lh / 2), "below": (cx, y1 + gap + lh / 2)}.get(pos, (cx, cy))
    grp["it"][-1]["p"]["k"] = [round(x + s["logo_dx"], 2), round(y + s["logo_dy"], 2)]
    r["parent"].insert(0, grp)
    return True


def _logo_replace(data, name, s, logo, shift_x=0.0):
    """Режим «лого»: надпись убирается, на её месте — картинка (вписана в область надписи)."""
    found = []
    O.find_nodes(data, [name], found)
    n = 0
    for node, parent in found:
        bb = O.path_bbox(node.get("it") if node.get("ty") == "gr" else node.get("shapes", []))
        if bb:
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            size = max(40, min(h * 1.8, w)) * s["logo_scale"] / 100
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        else:
            size, cx, cy = 100 * s["logo_scale"] / 100, 0, 0
        grp, _ = _logo_group(logo, s, size)
        grp["it"][-1]["p"]["k"] = [round(cx + shift_x + s["logo_dx"], 2), round(cy + s["logo_dy"], 2)]
        if node.get("ty") == "gr":
            tr = next((i for i in node.get("it", []) if i.get("ty") == "tr"), T.transform())
            node["it"] = [grp, tr]
        else:
            node["ty"] = 4
            node.pop("t", None)
            node["shapes"] = [grp]
        n += 1
    return n


def render(lottie, s, text, font_path, logo=None, part=None):
    """part=(i, N) — i-я часть баннера из N эмодзи: текст раскладывается на всю ширину и режется по частям."""
    data = copy.deepcopy(lottie)
    ip, op = data.get("ip", 0), data.get("op", 60)
    rep = {"removed": 0, "recolored": 0, "text": 0, "logo": 0, "fallback": False}
    mode = s.get("mode", "text")
    shift, mult, banner = 0.0, 1.0, None
    name = D.resolve_text(data, s["layer"])
    if part:
        i, n = part
        hits = []
        O.find_nodes(data, [name], hits)
        k = R.node_scale(data, hits[0][0]) if hits else 1.0      # единицы текста -> пиксели холста
        pitch = data.get("w", 512) / k                           # расстояние между соседними частями в единицах текста
        shift, mult = ((n - 1) / 2 - i) * pitch, float(n)
        banner = {"width": n * pitch * 0.94, "cap": data.get("h", 512) * 0.42 / k / 0.73}
    pats = list(s["remove"]) + (["*logo*", "*лого*"] if s["autologo"] else [])
    hits = []
    O.find_nodes(data, [name], hits)
    keep = [h[0] for h in hits]
    if pats:
        rep["removed"] = O.remove_nodes(data, pats, keep_nodes=keep)

    if s["fill"] or s["stroke"]:
        rep["recolored"] = O.recolor(data, s["fill"], s["stroke"], skip=[name, *s["skip"]],
                                     ip=ip, op=op, cycles=s["cycles"])

    if mode == "logo" and logo:
        n = _logo_replace(data, name, s, logo, shift)
        if not n:                                           # нет места под надпись — кладём лого внизу по центру
            lay = T.add_text_layer(data, "", font_path)
            lay["container"]["it"] = []
            grp, _ = _logo_group(logo, s, 110 * s["logo_scale"] / 100)
            grp["it"][-1]["p"]["k"] = [0, 0]
            lay["container"]["it"] = [grp, T.transform()]
            n = 1
            rep["fallback"] = True
        rep["logo"], rep["text"] = n, n
        return data, rep

    res = T.replace_text(data, name, text, font_path, s["size"], s["width"], shift, mult, banner)
    if not res:                                             # в файле нет надписи вообще — добавим слой с текстом
        res = [T.add_text_layer(data, text, font_path, s["size"], s["width"], shift, mult)]
        rep["fallback"] = True
    rep["node"] = name
    rep["text"] = len(res)
    for r in res:
        _style_text(r, s, ip, op)
    return data, rep
