"""Превью каталога: GIF-коллаж из эмодзи страницы с названиями под каждым."""
import io
import threading

from PIL import Image, ImageDraw, ImageFont

import render
import store

TILE, FR, PAD, TITLE_H, COLS = 176, 16, 10, 40, 4
BG, CARD = (22, 22, 30), (36, 36, 50)
_lock = threading.Lock()
_mem = {}
_fonts = {}


def _font(size):
    if size not in _fonts:
        for p in ("fonts/DejaVuSans-Bold.ttf", str(store.BASE / "fonts" / "DejaVuSans-Bold.ttf")):
            try:
                _fonts[size] = ImageFont.truetype(p, size)
                break
            except OSError:
                continue
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def _frames_of(data, n=FR, size=TILE):
    ip, op = data.get("ip", 0), data.get("op", 60)
    span = max(1, op - ip)
    out = []
    for i in range(n):
        im = render.render_frame(data, ip + span * i / n, size + 60, ss=1)
        out.append(im.resize((size, size), Image.LANCZOS))
    return out


def tile_frames(key):
    """Кадры плитки шаблона (для баннера — части в ряд). Кэш: память + диск."""
    meta = store.index().get(key)
    if not meta:
        return None
    parts = meta.get("parts")
    stamp = max([(store.TPL / store.index()[k]["file"]).stat().st_mtime for k in (parts or [key]) if k in store.index()] + [0])
    ck = (key, stamp)
    if ck in _mem:
        return _mem[ck]
    cdir = store.DATA / "previews"
    cdir.mkdir(parents=True, exist_ok=True)
    cf = cdir / f"{key}_{int(stamp)}.png"
    if cf.exists():
        strip = Image.open(cf).convert("RGBA")
        frames = [strip.crop((i * TILE, 0, (i + 1) * TILE, TILE)) for i in range(FR)]
    else:
        if parts:                                           # баннер: части склеиваем в одну плитку
            sub = [_frames_of(store.load_template(k), size=TILE) for k in parts if k in store.index()]
            w = TILE // max(1, len(sub))
            frames = []
            for i in range(FR):
                canvas = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
                for j, fs in enumerate(sub):
                    t = fs[i].resize((w, w), Image.LANCZOS)
                    canvas.alpha_composite(t, (j * w, (TILE - w) // 2))
                frames.append(canvas)
        else:
            frames = _frames_of(store.load_template(key))
        strip = Image.new("RGBA", (TILE * FR, TILE))
        for i, f in enumerate(frames):
            strip.paste(f, (i * TILE, 0))
        strip.save(cf)
    _mem[ck] = frames
    return frames


def _fit(draw, text, font, maxw):
    if draw.textlength(text, font=font) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def page_gif(items, start_no=1):
    """items: [(key, title)] -> GIF bytes. Номер в углу = номер кнопки."""
    with _lock:
        cells = [(k, t, tile_frames(k)) for k, t in items]
    n = len(cells)
    rows = (n + COLS - 1) // COLS
    cw, ch = TILE + PAD, TILE + TITLE_H + PAD
    W, H = COLS * cw + PAD, rows * ch + PAD
    frames = []
    for fi in range(FR):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        for idx, (key, title, fr) in enumerate(cells):
            x, y = PAD + (idx % COLS) * cw, PAD + (idx // COLS) * ch
            d.rounded_rectangle((x, y, x + TILE, y + TILE + TITLE_H - 6), 14, fill=CARD)
            if fr:
                tile = fr[fi]
                img.paste(tile.convert("RGB"), (x, y), tile.split()[3])
            d.ellipse((x + 6, y + 6, x + 32, y + 32), fill=(255, 214, 0))
            f = _font(16)
            no = str(start_no + idx)
            d.text((x + 19 - d.textlength(no, font=f) / 2, y + 10), no, fill=(20, 20, 20), font=f)
            ft = _font(15)
            for sz in (15, 14, 13, 12):
                ft = _font(sz)
                if d.textlength(title, font=ft) <= TILE - 8:
                    break
            t = _fit(d, title, ft, TILE - 8)
            d.text((x + TILE / 2 - d.textlength(t, font=ft) / 2, y + TILE + 6), t, fill=(240, 240, 245), font=ft)
        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=200))
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=130, loop=0, optimize=False, disposal=2)
    return buf.getvalue()


def warm_up():
    """Фоновая подготовка превью всех шаблонов (после старта)."""
    def run():
        for k in list(store.index()):
            try:
                tile_frames(k)
            except Exception:
                pass
    threading.Thread(target=run, daemon=True).start()
