"""Превью каталога: GIF-коллаж эмодзи страницы с названиями и подписью Emoji Owner."""
import concurrent.futures as cf
import hashlib
import io
import os
import threading

from PIL import Image, ImageDraw, ImageFont

import render
import store

TILE, FR, PAD, TITLE_H, COLS, FOOT_H = 176, 12, 10, 40, 4, 38
BG, CARD, GOLD = (22, 22, 30), (36, 36, 50), (255, 214, 0)
SIGNATURE = "Emoji Owner"
_lock = threading.Lock()
_mem = {}
_fonts = {}


def _font(size):
    if size not in _fonts:
        for p in (store.BASE / "fonts" / "DejaVuSans-Bold.ttf", "fonts/DejaVuSans-Bold.ttf"):
            try:
                _fonts[size] = ImageFont.truetype(str(p), size)
                break
            except OSError:
                continue
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def _file_hash(key):
    meta = store.index().get(key, {})
    if meta.get("parts"):
        return hashlib.md5("".join(_file_hash(k) for k in meta["parts"]).encode()).hexdigest()[:8]
    p = store.TPL / meta.get("file", "")
    return hashlib.md5(p.read_bytes()).hexdigest()[:8] if meta.get("file") and p.exists() else "none"


def _cache_paths(key):
    name = f"{key}_{_file_hash(key)}.png"
    return [store.DATA / "previews" / name, store.BUILTIN / "previews" / name]


def _frames_of(data, n=FR, size=TILE):
    ip, op = data.get("ip", 0), data.get("op", 60)
    span = max(1, op - ip)
    out = []
    for i in range(n):
        im = render.render_frame(data, ip + span * i / n, size + 40, ss=1)
        out.append(im.resize((size, size), Image.LANCZOS))
    return out


def _strip(frames):
    strip = Image.new("RGBA", (TILE * len(frames), TILE))
    for i, f in enumerate(frames):
        strip.paste(f, (i * TILE, 0))
    return strip


def _render_strip_png(key):
    """Рисует кадры шаблона и возвращает PNG-ленту (вызывается и в процессах-воркерах)."""
    buf = io.BytesIO()
    _strip(_frames_of(store.load_template(key))).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _placeholder():
    im = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((TILE // 2 - 8, TILE // 2 - 18), "?", fill=(120, 120, 140), font=_font(40))
    return [im] * FR


def _load_cached(key):
    for p in _cache_paths(key):
        if p.exists():
            strip = Image.open(p).convert("RGBA")
            n = strip.width // TILE
            return [strip.crop((i * TILE, 0, (i + 1) * TILE, TILE)) for i in range(n)]
    return None


def ensure(keys):
    """Готовит плитки для keys: кэш → (параллельная) отрисовка. Баннеры собираются из плиток частей."""
    singles = []
    for k in keys:
        for pk in (store.index().get(k, {}).get("parts") or [k]):
            if pk in store.index() and (pk, _file_hash(pk)) not in _mem and _load_cached(pk) is None:
                singles.append(pk)
    singles = list(dict.fromkeys(singles))
    if not singles:
        return
    (store.DATA / "previews").mkdir(parents=True, exist_ok=True)
    workers = min(len(singles), max(1, (os.cpu_count() or 1)), 4)
    results = {}
    if workers > 1:
        try:
            with cf.ProcessPoolExecutor(workers) as ex:
                for k, png in zip(singles, ex.map(_render_strip_png, singles)):
                    results[k] = png
        except Exception:
            results = {}
    for k in singles:
        try:
            png = results.get(k) or _render_strip_png(k)
            (store.DATA / "previews" / f"{k}_{_file_hash(k)}.png").write_bytes(png)
        except Exception:
            pass


def tile_frames(key):
    meta = store.index().get(key)
    if not meta:
        return _placeholder()
    ck = (key, _file_hash(key))
    if ck in _mem:
        return _mem[ck]
    try:
        if meta.get("parts"):
            ensure(meta["parts"])
            sub = [tile_frames(k) for k in meta["parts"] if k in store.index()]
            w = TILE // max(1, len(sub))
            frames = []
            for i in range(FR):
                canvas = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
                for j, fs in enumerate(sub):
                    canvas.alpha_composite(fs[i % len(fs)].resize((w, w), Image.LANCZOS), (j * w, (TILE - w) // 2))
                frames.append(canvas)
        else:
            frames = _load_cached(key)
            if frames is None:
                ensure([key])
                frames = _load_cached(key) or _placeholder()
    except Exception:
        frames = _placeholder()
    _mem[ck] = frames
    return frames


def _fit(draw, text, font, maxw):
    if draw.textlength(text, font=font) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def page_gif(items, start_no=1):
    """items: [(key, title)] -> GIF bytes: номера, названия под эмодзи, подпись Emoji Owner внизу."""
    with _lock:
        ensure([k for k, _ in items])
        cells = [(k, t, tile_frames(k)) for k, t in items]
    n = len(cells)
    rows = (n + COLS - 1) // COLS
    cw, ch = TILE + PAD, TILE + TITLE_H + PAD
    W, H = COLS * cw + PAD, rows * ch + PAD + FOOT_H
    nf = max(len(c[2]) for c in cells) if cells else 1
    frames = []
    for fi in range(nf):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        for idx, (key, title, fr) in enumerate(cells):
            x, y = PAD + (idx % COLS) * cw, PAD + (idx // COLS) * ch
            d.rounded_rectangle((x, y, x + TILE, y + TILE + TITLE_H - 6), 14, fill=CARD)
            tile = fr[fi % len(fr)]
            img.paste(tile.convert("RGB"), (x, y), tile.split()[3])
            d.ellipse((x + 6, y + 6, x + 34, y + 34), fill=GOLD)
            f = _font(16)
            no = str(start_no + idx)
            d.text((x + 20 - d.textlength(no, font=f) / 2, y + 11), no, fill=(20, 20, 20), font=f)
            for sz in (15, 14, 13, 12):
                ft = _font(sz)
                if d.textlength(title, font=ft) <= TILE - 8:
                    break
            t = _fit(d, title, ft, TILE - 8)
            d.text((x + TILE / 2 - d.textlength(t, font=ft) / 2, y + TILE + 6), t, fill=(240, 240, 245), font=ft)
        d.line((PAD, H - FOOT_H + 4, W - PAD, H - FOOT_H + 4), fill=(60, 60, 80), width=1)
        fs = _font(18)
        d.text((W / 2 - d.textlength(SIGNATURE, font=fs) / 2, H - FOOT_H + 11), SIGNATURE, fill=GOLD, font=fs)
        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=200))
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=150, loop=0, optimize=False, disposal=2)
    return buf.getvalue()


def warm_up():
    """Фоновая подготовка превью всех шаблонов (после старта)."""
    def run():
        try:
            ensure(list(store.index()))
            for k in list(store.index()):
                tile_frames(k)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
