"""Тексты и клавиатуры (чистые функции, без Telegram). Строка клавиатуры = [(подпись, callback_data)]."""
import html

import colors as C

esc = html.escape
LINE = "━━━━━━━━━━━━━━━━━━"
PAGE = 8

PRESETS = {
    "rainbow": ("🌈", "Радуга", "#ff0000 #ffd600 #00e676 #00b0ff #d500f9"),
    "fire": ("🔥", "Огонь", "#ff1744 #ff9100 #ffd600"),
    "ocean": ("🌊", "Океан", "#00e5ff #2979ff #1de9b6"),
    "neon": ("💜", "Неон", "#d500f9 #00e5ff #ff4081"),
    "candy": ("🍬", "Кэнди", "#ff80ab #b388ff #80d8ff"),
    "gold": ("👑", "Золото", "#ffd700 #fff3b0 #ff9800"),
    "ice": ("❄️", "Лёд", "#ffffff #80d8ff #448aff"),
    "sunset": ("🌅", "Закат", "#ff5252 #ff9100 #7c4dff"),
}
SWATCH = [("🟥", "#ff1744"), ("🟧", "#ff9100"), ("🟨", "#ffd600"), ("🟩", "#00e676"),
          ("🟦", "#2979ff"), ("🟪", "#aa00ff"), ("⬛", "#111111"), ("⬜", "#ffffff")]
TARGETS = {"all": "🎯 Всё сразу", "text_fill": "🔤 Текст · заливка", "text_stroke": "✒️ Текст · контур",
           "fill": "🎭 Эмодзи · заливка", "stroke": "🖍 Эмодзи · контур"}
# ключ: (шаг, мин, макс, название)
NUMS = {"size": (10, 20, 300, "Размер"), "stroke_w": (1, 0, 20, "Контур текста"),
        "cycles": (1, 1, 8, "Циклов"), "logo_size": (10, 20, 300, "Размер"),
        "logo_dx": (10, -400, 400, "X"), "logo_dy": (10, -400, 400, "Y"),
        "img_colors": (1, 2, 16, "Цветов"), "img_grid": (8, 24, 128, "Детализация")}
POS = {"left": "⬅️", "above": "⬆️", "right": "➡️", "below": "⬇️", "center": "⏺"}


def bar(text, rows):
    return text, rows


def spec_label(spec):
    if not spec:
        return "как в оригинале"
    cols, m = spec["colors"], spec["mode"]
    if m == "none":
        return "убрано"
    hexes = " ".join(C.to_hex(c) for c in cols)
    for emo, name, h in PRESETS.values():
        if [C.to_hex(c) for c in C.parse_spec(h)["colors"]] == [C.to_hex(c) for c in cols]:
            return f"{emo} {name}" + (" (перелив)" if m == "grad" else " (смена)")
    return {"static": hexes, "cycle": f"смена {hexes}", "grad": f"перелив {hexes}"}[m]


def _n(s, key):
    v = s.get(key)
    return int(v) if isinstance(v, float) and v == int(v) else v


def main_view(ud, s, tpl_title=None, notice=None, admin=False, found=None):
    logo = ud.get("logo")
    lines = ["✨ <b>TOXI Emoji Studio</b>", LINE]
    if notice:
        lines += [notice, LINE]
    lines += [
        f"🎭 Эмодзи: <b>{esc(tpl_title) if tpl_title else ('свой файл' if ud.get('lottie') else '— не выбрано —')}</b>",
        f"🔤 Текст: <code>{esc(ud.get('last_text') or '—')}</code>",
        f"🎨 Текст: {esc(spec_label(s['text_fill']))} · контур {esc(spec_label(s['text_stroke']))}",
        f"🎭 Эмодзи: заливка {esc(spec_label(s['fill']))} · контур {esc(spec_label(s['stroke']))}",
        f"🖼 Лого: {'✅ ' + ('вектор' if logo['kind'] == 'svg' else 'картинка') if logo else '—'}",
    ]
    if found:
        lines.append(f"🔎 Текст найден: <i>{esc(found)}</i>")
    lines += [LINE, "Напиши текст сообщением — соберу эмодзи 👇" if ud.get("lottie") else "Сначала выбери эмодзи 👇"]
    rows = [[("🎭 Эмодзи", "m:tpl:0"), ("✏️ Текст", "m:txt")],
            [("🎨 Цвета", "m:col"), ("🖼 Лого / картинка", "m:logo")],
            [("🧹 Удаление", "m:rm"), ("⚙️ Настройки", "m:set")],
            [("🚀 Собрать", "go")],
            [("💎 Премиум-эмодзи", "pack"), ("📎 Файл .tgs", "tgs")],
            [("📖 Гайд", "m:guide")]]
    if admin:
        rows.append([("🛠 Админ-панель", "a:main")])
    return "\n".join(lines), rows


def templates_view(idx, page):
    keys = list(idx)
    pages = max(1, (len(keys) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    chunk = keys[page * PAGE:(page + 1) * PAGE]
    rows, row = [], []
    for k in chunk:
        row.append((f"{idx[k].get('emoji', '🎭')} {idx[k].get('title', k)}", f"t:{k}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(("◀️", f"m:tpl:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("▶️", f"m:tpl:{page + 1}"))
    rows += [nav, [("🏠 Меню", "m:main")]]
    txt = f"🎭 <b>Выбери эмодзи</b>\n{LINE}\nВсего: {len(keys)}\nИли пришли свой <code>.json</code> / <code>.tgs</code> / анимированный стикер."
    return txt, rows


def text_view(s, fonts):
    def num(k, label):
        v = _n(s, k)
        return [("➖", f"n:{k}:-"), (f"{label}: {v}", "noop"), ("➕", f"n:{k}:+")]
    w = s["width"]
    rows = [num("size", "🔠 Размер"), num("stroke_w", "✒️ Контур")]
    rows.append([("↔️ Ширина: " + ("авто" if w is None else ("∞" if w == 0 else str(w))), "w:cycle"),
                 ("↩️ −20", "w:-"), ("↪️ +20", "w:+")])
    rows.append([("🔎 Автопоиск текста", "ly:auto"), ("🧭 Кандидаты", "ly:cand")])
    rows.append([("✍️ Имя слоя вручную", "ly:in")])
    if fonts:
        rows.append([("🔤 Шрифт: " + str(s["font"]).rsplit(".", 1)[0][:22] + "  ▶️", "f:next")])
    rows.append([("🏠 Меню", "m:main")])
    txt = (f"✏️ <b>Текст</b>\n{LINE}\nСлой с текстом: <code>{esc(str(s['layer']))}</code>\n"
           f"Шрифт: <code>{esc(str(s['font']))}</code>\nШирина: {'как у прежнего текста' if w is None else w}")
    return txt, rows


def candidates_view(cands):
    rows = [[(f"{i + 1}. {str(c['name'] or 'без имени')[:18]} · {round(c['score'])}", f"ly:{i}")] for i, c in enumerate(cands)]
    rows.append([("⬅️ Назад", "m:txt")])
    body = "\n".join(f"<b>{i + 1}.</b> {esc(c['path'])} — {esc(c['why'])}" for i, c in enumerate(cands)) or "Ничего похожего на текст не нашёл."
    return f"🧭 <b>Кандидаты на текст</b>\n{LINE}\n{body}\n\nВыбери нужный:", rows


def colors_view(ud, s):
    t = ud.get("target", "all")
    rows = [[(("🔘 " if k == t else "") + v, f"c:{k}")] for k, v in list(TARGETS.items())[:1]]
    rows += [[(("🔘 " if k == t else "") + TARGETS[k], f"c:{k}") for k in ("text_fill", "text_stroke")],
             [(("🔘 " if k == t else "") + TARGETS[k], f"c:{k}") for k in ("fill", "stroke")]]
    rows.append([(emo, f"cs:{i}") for i, (emo, _) in enumerate(SWATCH)])
    pk = list(PRESETS)
    for i in range(0, len(pk), 4):
        rows.append([(f"{PRESETS[k][0]} {PRESETS[k][1]}", f"cp:{k}") for k in pk[i:i + 4]])
    mode = ud.get("fxmode", "grad")
    rows.append([(("🌊 Режим: перелив" if mode == "grad" else "🔁 Режим: смена"), "cm"),
                 ("➖", "n:cycles:-"), (f"циклов {_n(s, 'cycles')}", "noop"), ("➕", "n:cycles:+")])
    rows.append([("✍️ Свой цвет", "cu"), ("♻️ Сброс", "cr"), ("🚫 Убрать", "cn")])
    rows.append([("🏠 Меню", "m:main")])
    cur = {"all": "—", "text_fill": s["text_fill"], "text_stroke": s["text_stroke"], "fill": s["fill"], "stroke": s["stroke"]}[t]
    txt = (f"🎨 <b>Цвета</b>\n{LINE}\nЦель: <b>{TARGETS[t]}</b>\nСейчас: {esc(spec_label(cur)) if t != 'all' else 'применится ко всем четырём'}\n\n"
           f"🟥 — один цвет · пресет — переливается всё: от контура до текста.\n"
           f"Режим <i>перелив</i>: по эмодзи бежит волна цветов, <i>смена</i>: всё меняется синхронно.")
    return txt, rows


def logo_view(ud, s):
    logo = ud.get("logo")
    rows = [[(("🔘" if s["logo_pos"] == p else "") + e, f"l:{p}") for p, e in POS.items()],
            [("◀️", "n:logo_dx:-"), ("🔼", "n:logo_dy:-"), ("🔽", "n:logo_dy:+"), ("▶️", "n:logo_dx:+")],
            [("➖", "n:logo_size:-"), (f"размер {_n(s, 'logo_size')}", "noop"), ("➕", "n:logo_size:+")]]
    if logo and logo["kind"] == "img":
        rows.append([("➖", "n:img_colors:-"), (f"цветов {_n(s, 'img_colors')}", "noop"), ("➕", "n:img_colors:+")])
        rows.append([("➖", "n:img_grid:-"), (f"детали {_n(s, 'img_grid')}", "noop"), ("➕", "n:img_grid:+")])
        rows.append([(("✅" if s["img_bg"] else "⬜") + " убирать фон", "lb")])
    rows.append([("🎨 Цвет лого", "lc"), ("🗑 Убрать лого", "lr")])
    rows.append([("🏠 Меню", "m:main")])
    txt = (f"🖼 <b>Лого / картинка</b>\n{LINE}\nСтатус: {'✅ загружено (' + ('SVG' if logo['kind'] == 'svg' else 'картинка → вектор') + ')' if logo else 'нет'}\n"
           f"Положение: <b>{s['logo_pos']}</b> · сдвиг {_n(s, 'logo_dx')}/{_n(s, 'logo_dy')} · цвет: {esc(spec_label(s['logo_color']))}\n\n"
           "Пришли <b>SVG</b>, <b>PNG</b>, <b>JPG</b> или статичный стикер — картинка будет автоматически превращена в вектор "
           "(в TGS нельзя растр). Для лого лучше контрастная картинка на однотонном фоне.")
    return txt, rows


def remove_view(s):
    rows = [[(("✅" if s["autologo"] else "⬜") + " Авто-удаление «logo / лого»", "al")],
            [("➕ Добавить маску", "ri"), ("🧽 Очистить", "rc")],
            [("🌲 Структура файла", "tree")], [("🏠 Меню", "m:main")]]
    txt = (f"🧹 <b>Удаление слоёв и лого</b>\n{LINE}\nАвто «logo/лого»: {'вкл' if s['autologo'] else 'выкл'}\n"
           f"Маски удаления: <code>{esc(', '.join(s['remove']) or '—')}</code>\n"
           f"Не красить: <code>{esc(', '.join(s['skip']) or '—')}</code>\n\n"
           "Маска — имя слоя/папки, <code>*</code> = любая часть: <code>logo*</code>, <code>*ruka*</code>.")
    rows.insert(2, [("🚫 Не красить…", "si")])
    return txt, rows


def settings_view(ud, s):
    txt = (f"⚙️ <b>Настройки</b>\n{LINE}\nТекст-слой: <code>{esc(str(s['layer']))}</code> · размер {_n(s, 'size')} · шрифт <code>{esc(str(s['font']))}</code>\n"
           f"Текст: {esc(spec_label(s['text_fill']))} / контур {esc(spec_label(s['text_stroke']))}\n"
           f"Эмодзи: {esc(spec_label(s['fill']))} / контур {esc(spec_label(s['stroke']))}\n"
           f"Циклов: {_n(s, 'cycles')} · лого: {'есть' if ud.get('logo') else 'нет'}")
    return txt, [[("♻️ Сбросить всё", "reset")], [("🏠 Меню", "m:main")]]


GUIDE = f"""📖 <b>Гайд</b>
{LINE}
<b>Как пользоваться</b>
1. 🎭 Выбери эмодзи (или пришли свой <code>.json/.tgs</code>)
2. Напиши текст — получишь анимированный стикер
3. 🎨 красит текст, контур и всё эмодзи; пресеты <i>переливаются</i>
4. 🖼 пришли SVG/PNG/JPG — добавлю как лого
5. 💎 «Премиум-эмодзи» — добавит результат в твой пак кастомных эмодзи

<b>Как подготовить свой эмодзи</b>
• Холст 512×512, ≤ 3 сек, итоговый .tgs ≤ 64 КБ
• Надпись — это <b>группа</b> (или слой) из векторных букв. Назови её <code>надпись</code> или <code>text</code>
  (подойдут также txt, trst, nick, HELL, TextGroup…) — бот найдёт сам, даже если имя другое: он узнаёт ряд букв по форме
• Внутри группы оставь заливку (и контур) — стиль сохранится
• Лого/значки назови <code>logo</code> — они удалятся автоматически; папка <code>LOGO</code> станет местом под твоё лого
• Текстовые слои Lottie (ty 5) тоже понимаю — превращу в вектор

<b>Добавить в бота навсегда</b> (админ): 🛠 Админ-панель → ➕ Добавить шаблон → кинь .json/.tgs"""


def guide_view():
    return GUIDE, [[("🏠 Меню", "m:main")]]


# ---------------- админка ----------------
def admin_main(stats):
    txt = (f"🛠 <b>Админ-панель</b>\n{LINE}\n👥 Пользователей: <b>{stats['users']}</b> (за сутки: {stats['day']})\n"
           f"🎞 Сборок: <b>{stats['renders']}</b>\n📦 Шаблонов: <b>{stats['tpls']}</b>\n"
           f"💾 Хранилище: <code>{esc(stats['path'])}</code>{'' if stats['persistent'] else chr(10) + '⚠️ Нет Volume — при редеплое новые шаблоны пропадут. Подключи Volume к /data'}")
    return txt, [[("📦 Шаблоны", "a:tpls:0"), ("➕ Добавить шаблон", "a:add")],
                 [("📣 Рассылка", "a:bc")], [("🏠 Меню", "m:main")]]


def admin_tpls(idx, page):
    keys = list(idx)
    pages = max(1, (len(keys) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows = [[(f"{idx[k].get('emoji', '🎭')} {idx[k].get('title', k)}", f"a:t:{k}")] for k in keys[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0: nav.append(("◀️", f"a:tpls:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1: nav.append(("▶️", f"a:tpls:{page + 1}"))
    rows += [nav, [("➕ Добавить", "a:add"), ("⬅️ Назад", "a:main")]]
    return f"📦 <b>Шаблоны</b> ({len(keys)})\n{LINE}\nВыбери, чтобы изменить или удалить.", rows


def admin_tpl(key, meta, kb):
    st = meta.get("settings", {})
    txt = (f"{meta.get('emoji', '🎭')} <b>{esc(meta.get('title', key))}</b>\n{LINE}\nКлюч: <code>{esc(key)}</code>\n"
           f"Размер: {kb} КБ\nТекстовый слой: <code>{esc(str(st.get('layer', 'auto')))}</code>")
    rows = [[("🧪 Тест", f"a:test:{key}"), ("✏️ Название", f"a:ren:{key}")],
            [("🎯 Текстовый слой", f"a:ly:{key}"), ("🗑 Удалить", f"a:del:{key}")],
            [("⬅️ К списку", "a:tpls:0")]]
    return txt, rows


def admin_confirm_delete(key, meta):
    return (f"🗑 Удалить «{esc(meta.get('title', key))}»? Это необратимо.",
            [[("✅ Да, удалить", f"a:delok:{key}"), ("↩️ Отмена", f"a:t:{key}")]])


def admin_layer(key, cands):
    rows = [[(f"{i + 1}. {str(c['name'] or 'без имени')[:18]} · {round(c['score'])}", f"a:lys:{key}:{i}")] for i, c in enumerate(cands)]
    rows.append([("⬅️ Назад", f"a:t:{key}")])
    body = "\n".join(f"<b>{i + 1}.</b> {esc(c['path'])} — {esc(c['why'])}" for i, c in enumerate(cands)) or "Кандидатов нет."
    return f"🎯 <b>Текстовый слой</b>\n{LINE}\n{body}", rows


def admin_add():
    return (f"➕ <b>Добавление шаблонов</b>\n{LINE}\nКидай <code>.json</code> или <code>.tgs</code> — по одному или пачкой.\n"
            "Название = подпись к файлу (или имя файла). Текстовую папку найду сам.",
            [[("✅ Готово", "a:main")]])
