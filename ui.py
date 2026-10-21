"""Экраны и клавиатуры (чистые функции, без Telegram). Строка клавиатуры = [(подпись, callback_data)]."""
import html

import colors as C
import pricing as PR

esc = html.escape
LINE = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
PAGE = 12

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
# темы: красят всё сразу. Значения: спецификация цветов для (эмодзи-заливка, эмодзи-контур, текст-заливка, текст-контур)
THEMES = {
    "keep": ("↩️", "Как в оригинале", None),
    "redblack": ("🔴", "Красно-чёрная", ("#e5173f", "#111111", "#ffffff", "#e5173f")),
    "blackred": ("⚫", "Чёрно-красная", ("#141414", "#ff1744", "#ff1744", "#ffffff")),
    "bluewhite": ("🔵", "Сине-белая", ("#2979ff", "#ffffff", "#ffffff", "#1a47a0")),
    "goldblack": ("🟡", "Золото-чёрная", ("#e0a800", "#1a1a1a", "#1a1a1a", "#ffd700")),
    "rainbow": ("🌈", "Радуга (перелив)", "preset:rainbow"),
    "fire": ("🔥", "Огонь (перелив)", "preset:fire"),
    "ocean": ("🌊", "Океан (перелив)", "preset:ocean"),
    "neon": ("💜", "Неон (перелив)", "preset:neon"),
    "gold": ("👑", "Золото (перелив)", "preset:gold"),
}
SWATCH = [("🟥", "#ff1744"), ("🟧", "#ff9100"), ("🟨", "#ffd600"), ("🟩", "#00e676"),
          ("🟦", "#2979ff"), ("🟪", "#aa00ff"), ("⬛", "#111111"), ("⬜", "#ffffff")]
TARGETS = {"all": "🎯 Всё сразу", "text_fill": "🔤 Текст · заливка", "text_stroke": "✒️ Текст · контур",
           "fill": "🎭 Эмодзи · заливка", "stroke": "🖍 Эмодзи · контур"}
NUMS = {"size": (10, 20, 300, "Размер"), "stroke_w": (1, 0, 20, "Контур"), "cycles": (1, 1, 8, "Циклов"),
        "logo_scale": (10, 20, 400, "Масштаб"), "logo_dx": (10, -400, 400, "X"), "logo_dy": (10, -400, 400, "Y"),
        "img_colors": (1, 2, 16, "Цветов"), "img_grid": (8, 24, 128, "Детализация")}
STEP_NAMES = ["Эмодзи", "Что вставить", "Покраска", "Ввод"]


def spec_label(spec):
    if not spec:
        return "как есть"
    cols, m = spec["colors"], spec["mode"]
    if m == "none":
        return "убрано"
    hexes = " ".join(C.to_hex(c) for c in cols)
    for emo, name, h in PRESETS.values():
        if [C.to_hex(c) for c in C.parse_spec(h)["colors"]] == [C.to_hex(c) for c in cols]:
            return f"{emo} {name}"
    return {"static": hexes, "cycle": f"смена {hexes}", "grad": f"перелив {hexes}"}[m]


def theme_id(s):
    """Какая тема сейчас применена (по четырём цветам) или None."""
    cur = tuple(spec_label(s[k]) for k in ("fill", "stroke", "text_fill", "text_stroke"))
    if all(c == "как есть" for c in cur):
        return "keep"
    for tid, (_, _, val) in THEMES.items():
        if isinstance(val, tuple):
            want = tuple(C.to_hex(C.parse_color(v)) for v in val)
            have = tuple((s[k]["colors"][0] and C.to_hex(s[k]["colors"][0])) if s[k] and s[k]["mode"] == "static" else None
                         for k in ("fill", "stroke", "text_fill", "text_stroke"))
            if want == have:
                return tid
        elif isinstance(val, str) and val.startswith("preset:"):
            lab = PRESETS[val[7:]]
            if all(spec_label(s[k]) == f"{lab[0]} {lab[1]}" for k in ("fill", "stroke", "text_fill", "text_stroke")):
                return tid
    return None


def _n(s, key):
    v = s.get(key)
    return int(v) if isinstance(v, float) and v == int(v) else v


def head(step, title):
    dots = "".join("🟣" if i < step else "⚪" for i in range(4))
    return f"{dots}  <b>Шаг {step} из 4 · {title}</b>\n{LINE}\n"


# ------------------------------------------------------------------ главная
def bal_text(bal):
    return "∞ (админ)" if bal is None else f"{bal} шт."


def home_view(ud, s, admin=False, notice=None, bal=0):
    mode = "🔤 Текст" if s["mode"] == "text" else "🖼 Лого"
    txt = ("✨ <b>TOXI Studio</b>\n"
           "<i>Анимированные эмодзи и стикеры с твоим текстом или лого</i>\n" + LINE + "\n")
    if notice:
        txt += notice + "\n" + LINE + "\n"
    txt += ("1️⃣ выбираешь эмодзи по превью\n2️⃣ текст <b>или</b> лого\n3️⃣ покраска одним нажатием\n"
            "4️⃣ получаешь стикеры или сразу пак\n\n"
            f"💰 Баланс: <b>{bal_text(bal)}</b>  ·  1 эмодзи = 1 шт.\n"
            f"Режим сейчас: <b>{mode}</b>\n<i>© Emoji Owner</i>")
    rows = [[("🚀 Создать эмодзи", "n")],
            [("🎞 Каталог", "n"), ("📦 Мои паки", "packs")],
            [("💳 Купить штуки", "shop"), ("💰 Баланс", "bal")],
            [("📖 Как это работает", "guide")]]
    if admin:
        rows.append([("🛠 Админ-панель", "a:main")])
    return txt, rows


# ------------------------------------------------------------------ шаг 1: каталог
def catalog_view(idx, page, sel, titles_sel):
    keys = list(idx)
    pages = max(1, (len(keys) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    chunk = keys[page * PAGE:(page + 1) * PAGE]
    base = page * PAGE
    names = "\n".join(f"<b>{base + i + 1}.</b> {esc(idx[k].get('title', k))}" + (" 🧩" if idx[k].get("parts") else "")
                      + ("  ✅" if k in sel else "") for i, k in enumerate(chunk))
    chosen = (", ".join(esc(t) for t in titles_sel[:4]) + ("…" if len(titles_sel) > 4 else "")) if titles_sel else "ничего"
    cap = (head(1, "Эмодзи") + f"Стр. {page + 1}/{pages} · нажми номер, чтобы выбрать (можно несколько)\n\n{names}\n\n"
           f"Выбрано: <b>{len(sel)}</b> — {chosen}\n<i>© Emoji Owner</i>")
    rows = []
    for a in range(0, len(chunk), 4):
        rows.append([((("✅ " if k in sel else "") + str(page * PAGE + a + j + 1)), f"ct:{page}:{a + j}") for j, k in enumerate(chunk[a:a + 4])])
    nav = []
    if page > 0: nav.append(("◀️", f"cat:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1: nav.append(("▶️", f"cat:{page + 1}"))
    rows.append(nav)
    rows.append([("👁 Выбранные стикерами", "pv:sel"), ("🎬 Вся страница стикерами", "pv:page")])
    rows.append([("🏠 Меню", "h"), (f"Дальше ▶️ ({len(sel)})" if sel else "Дальше ▶️", "cn")])
    return cap, rows, page, chunk


# ------------------------------------------------------------------ шаг 2: тумблер текст/лого
def mode_view(s, titles):
    t = s["mode"] == "text"
    txt = (head(2, "Что вставить") + f"Эмодзи: <b>{esc(', '.join(titles[:3]) + ('…' if len(titles) > 3 else ''))}</b>\n\n"
           "Переключатель — выбери <b>одно</b>:\n"
           f"{'🔘' if t else '⚪'} <b>Текст</b> — надпись заменится твоим текстом, логотипы шаблона удалятся\n"
           f"{'🔘' if not t else '⚪'} <b>Лого</b> — надпись убирается, на её месте будет твоя картинка (SVG / PNG / JPG)")
    sw = [("🔘 🔤 Текст" if t else "⚪ 🔤 Текст", "md:text"), ("🔘 🖼 Лого" if not t else "⚪ 🖼 Лого", "md:logo")]
    nb = s.get("banner", 0)
    many = len(titles) > 1
    txt += ("\n\n<b>Раскладка</b>:\n"
            f"{'⚪' if nb else '🔘'} <b>Отдельные</b> — каждое эмодзи само по себе\n"
            f"{'🔘' if nb else '⚪'} <b>Баннер 🧩</b> — текст растягивается на несколько эмодзи подряд, "
            + ("части = выбранные эмодзи по порядку" if many else f"выбранное эмодзи повторится {nb or 3} раза(ов) подряд"))
    lay = [("⚪ Отдельные" if nb else "🔘 Отдельные", "bn:0"), ("🔘 🧩 Баннер" if nb else "⚪ 🧩 Баннер", "bn:on")]
    rows = [sw, lay]
    if nb and not many:
        rows.append([("➖", "bn:-"), (f"частей: {nb}", "noop"), ("➕", "bn:+")])
    rows.append([("◀️ Назад", "b:cat"), ("Дальше ▶️", "mn")])
    return txt, rows


# ------------------------------------------------------------------ шаг 3: покраска
def paint_view(ud, s):
    cur = theme_id(s)
    txt = (head(3, "Покраска") + "Выбери готовую тему — красится <b>всё: эмодзи, контур и " +
           ("текст" if s["mode"] == "text" else "лого") + "</b>.\n"
           "Темы с <i>(перелив)</i> переливаются по всему эмодзи.\n\n"
           f"Сейчас: <b>{esc(THEMES[cur][1]) if cur else 'своя настройка'}</b>")
    rows, row = [], []
    for tid, (emo, name, _) in THEMES.items():
        row.append((("✅ " if tid == cur else emo + " ") + name, f"th:{tid}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([("🎨 Тонкая настройка", "adv")])
    rows.append([("◀️ Назад", "b:mode"), ("Дальше ▶️", "pn")])
    return txt, rows


# ------------------------------------------------------------------ шаг 4: ввод
def input_view(ud, s, titles, has_logo):
    n = len(titles)
    if s["mode"] == "text":
        body = ("✏️ <b>Пришли текст</b> сообщением.\n\n"
                "• Каждая строка = отдельное эмодзи\n• Можно кириллицу и латиницу\n"
                f"• Баннеры 🧩 разрежут текст на части сами\n\nВыбрано шаблонов: <b>{n}</b>")
        rows = [[("◀️ Назад", "b:paint"), ("🏠 Меню", "h")]]
    else:
        body = ("🖼 <b>Пришли лого</b>: файл <code>.svg</code>, фото/PNG/JPG/WEBP или статичный стикер.\n\n"
                "• Картинку я сам превращу в вектор (в TGS растр нельзя)\n• Лучше контрастная, на однотонном фоне\n\n" +
                ("✅ Лого уже загружено — можно собирать." if has_logo else "Лого пока нет."))
        rows = [[("🚀 Собрать с этим лого", "pend")]] if has_logo else []
        rows.append([("◀️ Назад", "b:paint"), ("🏠 Меню", "h")])
    return head(4, "Ввод") + body, rows


def dest_view(n_items, lines, mode, parts_total, bal=0):
    sample = ""
    if mode == "text" and lines:
        sample = "Текст: " + ", ".join(f"<code>{esc(x)}</code>" for x in lines[:3]) + ("…" if len(lines) > 3 else "") + "\n"
    cost = f"спишется <b>{parts_total} шт.</b>" + ("" if bal is None else f" · баланс <b>{bal} шт.</b>")
    txt = (f"📦 <b>Куда отправить?</b>\n{LINE}\n{sample}Будет создано: <b>{parts_total} шт.</b> — {cost}\n\n"
           "📩 <b>Прислать</b> — получишь стикеры в чат\n"
           "💎 <b>Эмодзи-пак</b> — кастомные эмодзи (для Telegram Premium)\n"
           "🎭 <b>Стикер-пак</b> — обычные анимированные стикеры")
    return txt, [[("📩 Прислать сюда", "go:send")], [("💎 В эмодзи-пак", "pk:emoji"), ("🎭 В стикер-пак", "pk:sticker")],
                 [("❌ Отмена", "h")]]


def pack_choose_view(kind, packs, total):
    name = "эмодзи-пак" if kind == "emoji" else "стикер-пак"
    cap = 200 if kind == "emoji" else 120
    txt = f"{'💎' if kind == 'emoji' else '🎭'} <b>В какой {name}?</b>\n{LINE}\nДобавляем: <b>{total} шт.</b>"
    rows = [[("🆕 Создать новый пак", f"go:new{kind}")]]
    for k, p in enumerate(packs[-4:][::-1]):
        idx = len(packs) - 1 - k
        free = cap - p.get("count", 0)
        if free >= total:
            rows.append([(f"➕ В «{p['title'][:22]}» ({p.get('count', 0)}/{cap})", f"go:add{kind}:{idx}")])
    rows.append([("◀️ Назад", "pend_back"), ("❌ Отмена", "h")])
    return txt, rows


def nounits_view(need, have):
    txt = (f"⛔️ <b>Закончились штуки эмодзи, купите</b>\n{LINE}\nНужно: <b>{need} шт.</b> · на балансе: <b>{have} шт.</b>")
    return txt, [[("💳 Купить штуки", "shop")], [("🏠 Меню", "h")]]


def balance_view(bal, uid):
    txt = (f"💰 <b>Твой баланс</b>\n{LINE}\nШтук: <b>{bal_text(bal)}</b>\nID: <code>{uid}</code>\n\n"
           "1 созданное эмодзи (или одна часть баннера) = 1 шт.")
    return txt, [[("💳 Купить штуки", "shop")], [("🏠 Меню", "h")]]


# ------------------------------------------------------------------ магазин
def shop_view():
    def line(i):
        it = PR.item(i)
        r, d = PR.with_fee(it)
        return f"• <b>{it['title']}</b> — {PR.fmt_rub(r)} / {PR.fmt_usd(d)}"
    pieces = "\n".join(line(f"n{q}") for q in (1, 5, 10, 15, *PR.RANGE_BUTTONS))
    packs = "\n".join(line(p) + f"  → +{PR.item(p)['units']} шт." for p in PR.PACKS)
    txt = (f"💳 <b>Купить штуки</b>\n{LINE}\n<i>Цены с комиссией: рубли +{PR.RUB_FEE * 100:g}%, крипта +{PR.USD_FEE * 100:g}%</i>\n\n"
           f"<b>Штуки</b>\n{pieces}\n<i>между 20 и 100 шт. цена растёт линейно</i>\n\n<b>Паки со скидкой</b>\n{packs}\n\nВыбери 👇")
    rows = [[(f"{q} шт.", f"sh:n{q}") for q in (1, 5, 10, 15)],
            [(f"{q}", f"sh:n{q}") for q in PR.RANGE_BUTTONS],
            [(f"🎁 {PR.PACKS[p][0]}+{PR.PACKS[p][1]}", f"sh:{p}") for p in PR.PACKS],
            [("🏠 Меню", "h")]]
    return txt, rows


def method_view(item_id):
    it = PR.item(item_id)
    r, d = PR.with_fee(it)
    txt = (f"🛒 <b>{it['title']}</b>  → <b>+{it['units']} шт.</b>\n{LINE}\n"
           f"🇷🇺 Рубли: <b>{PR.fmt_rub(r)}</b> <i>(с комиссией {PR.RUB_FEE * 100:g}%)</i>\n"
           f"🪙 Крипта: <b>{PR.fmt_usd(d)}</b> <i>(с комиссией {PR.USD_FEE * 100:g}%)</i>\n\nКак платишь?")
    return txt, [[(f"🇷🇺 Рубли · {PR.fmt_rub(r)}", f"pay:{item_id}:rub")], [(f"🪙 Крипта · {PR.fmt_usd(d)}", f"pay:{item_id}:usd")],
                 [("◀️ Назад", "shop")]]


def order_view(oid, o, info):
    txt = (f"🧾 <b>Заказ #{oid}</b>\n{LINE}\n{esc(o['title'])} → <b>+{o['units']} шт.</b>\nК оплате: <b>{esc(o['amount'])}</b>\n\n"
           f"{info}\n\n💬 В комментарии к оплате укажи: <code>#{oid}</code>\nПосле оплаты нажми кнопку — админ проверит и начислит штуки.")
    return txt, [[("✅ Я оплатил", f"paid:{oid}")], [("❌ Отмена", f"pcancel:{oid}")]]


def done_view(s, notice):
    mode = "🔤 Текст" if s["mode"] == "text" else "🖼 Лого"
    txt = f"{notice}\n{LINE}\nРежим: <b>{mode}</b> — что дальше?"
    return txt, [[("✏️ Другой текст" if s["mode"] == "text" else "🖼 Другое лого", "again"), ("🎨 Перекрасить", "repaint")],
                 [("🔄 Сменить режим", "b:mode"), ("🎞 Другие эмодзи", "b:cat")], [("🏠 Меню", "h")]]


# ------------------------------------------------------------------ тонкая настройка
def adv_view(s):
    txt = f"🎛 <b>Тонкая настройка</b>\n{LINE}\nТут всё по отдельности: цвета по частям, шрифт и размер, удаление слоёв."
    return txt, [[("🎨 Цвета по частям", "m:col"), ("🔠 Текст и шрифт", "m:txt")],
                 [("🖼 Лого", "m:logo"), ("🧹 Удаление", "m:rm")], [("⚙️ Итоги", "m:set"), ("◀️ К покраске", "b:paint")]]


def text_view(s, fonts):
    def num(k, label):
        return [("➖", f"n:{k}:-"), (f"{label}: {_n(s, k)}", "noop"), ("➕", f"n:{k}:+")]
    w = s["width"]
    rows = [num("size", "🔠 Размер"), num("stroke_w", "✒️ Контур")]
    rows.append([("↔️ Ширина: " + ("авто" if w is None else ("∞" if w == 0 else str(w))), "w:cycle"), ("−20", "w:-"), ("+20", "w:+")])
    rows.append([("🔎 Автопоиск текста", "ly:auto"), ("🧭 Кандидаты", "ly:cand")])
    rows.append([("✍️ Имя слоя вручную", "ly:in")])
    if fonts:
        rows.append([("🔤 Шрифт: " + str(s["font"]).rsplit(".", 1)[0][:22] + "  ▶️", "f:next")])
    rows.append([("◀️ Назад", "adv")])
    return (f"🔠 <b>Текст и шрифт</b>\n{LINE}\nСлой с текстом: <code>{esc(str(s['layer']))}</code>\n"
            f"Шрифт: <code>{esc(str(s['font']))}</code>\nШирина: {'как у прежнего текста' if w is None else w}"), rows


def candidates_view(cands):
    rows = [[(f"{i + 1}. {str(c['name'] or 'без имени')[:18]} · {round(c['score'])}", f"ly:{i}")] for i, c in enumerate(cands)]
    rows.append([("◀️ Назад", "m:txt")])
    body = "\n".join(f"<b>{i + 1}.</b> {esc(c['path'])} — {esc(c['why'])}" for i, c in enumerate(cands)) or "Ничего похожего на текст не нашёл."
    return f"🧭 <b>Кандидаты на текст</b>\n{LINE}\n{body}\n\nВыбери нужный:", rows


def colors_view(ud, s):
    t = ud.get("target", "all")
    rows = [[(("🔘 " if t == "all" else "") + TARGETS["all"], "c:all")],
            [(("🔘 " if k == t else "") + TARGETS[k], f"c:{k}") for k in ("text_fill", "text_stroke")],
            [(("🔘 " if k == t else "") + TARGETS[k], f"c:{k}") for k in ("fill", "stroke")],
            [(emo, f"cs:{i}") for i, (emo, _) in enumerate(SWATCH)]]
    pk = list(PRESETS)
    for i in range(0, len(pk), 4):
        rows.append([(f"{PRESETS[k][0]} {PRESETS[k][1]}", f"cp:{k}") for k in pk[i:i + 4]])
    mode = ud.get("fxmode", "grad")
    rows.append([(("🌊 Режим: перелив" if mode == "grad" else "🔁 Режим: смена"), "cm"),
                 ("➖", "n:cycles:-"), (f"циклов {_n(s, 'cycles')}", "noop"), ("➕", "n:cycles:+")])
    rows.append([("✍️ Свой цвет", "cu"), ("♻️ Сброс", "cr"), ("🚫 Убрать", "cn_")])
    rows.append([("◀️ Назад", "adv")])
    cur = {"all": None, "text_fill": s["text_fill"], "text_stroke": s["text_stroke"], "fill": s["fill"], "stroke": s["stroke"]}[t]
    return (f"🎨 <b>Цвета по частям</b>\n{LINE}\nЦель: <b>{TARGETS[t]}</b>\n"
            f"Сейчас: {esc(spec_label(cur)) if t != 'all' else 'применится ко всем четырём'}\n\n"
            "🟥 — один цвет · пресет — переливается всё.\n<i>Перелив</i>: волна цветов, <i>смена</i>: всё синхронно."), rows


def logo_view(ud, s):
    logo = ud.get("logo")
    rows = [[("➖", "n:logo_scale:-"), (f"масштаб {_n(s, 'logo_scale')}%", "noop"), ("➕", "n:logo_scale:+")],
            [("◀️", "n:logo_dx:-"), ("🔼", "n:logo_dy:-"), ("🔽", "n:logo_dy:+"), ("▶️", "n:logo_dx:+")]]
    if logo and logo["kind"] == "img":
        rows.append([("➖", "n:img_colors:-"), (f"цветов {_n(s, 'img_colors')}", "noop"), ("➕", "n:img_colors:+")])
        rows.append([("➖", "n:img_grid:-"), (f"детали {_n(s, 'img_grid')}", "noop"), ("➕", "n:img_grid:+")])
        rows.append([(("✅" if s["img_bg"] else "⬜") + " убирать фон", "lb")])
    rows.append([("🎨 Цвет лого", "lc"), ("🗑 Убрать лого", "lr")])
    rows.append([("◀️ Назад", "adv")])
    return (f"🖼 <b>Лого</b>\n{LINE}\nСтатус: {'✅ ' + ('SVG' if logo['kind'] == 'svg' else 'картинка → вектор') if logo else 'нет — пришли файл'}\n"
            f"Сдвиг: {_n(s, 'logo_dx')}/{_n(s, 'logo_dy')} · цвет: {esc(spec_label(s['logo_color']))}"), rows


def remove_view(s):
    rows = [[(("✅" if s["autologo"] else "⬜") + " Авто-удаление «logo / лого»", "al")],
            [("➕ Что удалить", "ri"), ("🚫 Не красить", "si")], [("🧽 Очистить", "rc"), ("🌲 Структура", "tree")],
            [("◀️ Назад", "adv")]]
    return (f"🧹 <b>Удаление слоёв</b>\n{LINE}\nАвто «logo/лого»: {'вкл' if s['autologo'] else 'выкл'}\n"
            f"Удалять: <code>{esc(', '.join(s['remove']) or '—')}</code>\nНе красить: <code>{esc(', '.join(s['skip']) or '—')}</code>\n\n"
            "Маска: <code>*</code> = любая часть имени, например <code>ruka*</code>."), rows


def settings_view(ud, s):
    return (f"⚙️ <b>Итоги настроек</b>\n{LINE}\nРежим: {'текст' if s['mode'] == 'text' else 'лого'} · слой <code>{esc(str(s['layer']))}</code> · "
            f"размер {_n(s, 'size')} · шрифт <code>{esc(str(s['font']))}</code>\n"
            f"Текст: {esc(spec_label(s['text_fill']))} / контур {esc(spec_label(s['text_stroke']))}\n"
            f"Эмодзи: {esc(spec_label(s['fill']))} / контур {esc(spec_label(s['stroke']))}\n"
            f"Лого: {'есть' if ud.get('logo') else 'нет'}"), [[("♻️ Сбросить всё", "reset")], [("◀️ Назад", "adv")]]


GUIDE = f"""📖 <b>Как это работает</b>
{LINE}
<b>Быстрый путь</b>
🚀 Создать → выбираешь эмодзи по превью → тумблер <b>Текст / Лого</b> → тема покраски → текст → куда отправить.

<b>Что умею</b>
• 🔤 заменять надпись твоим текстом (можно несколько строк сразу — каждая станет эмодзи)
• 🖼 вместо надписи ставить твоё лого (SVG, PNG, JPG — превращу в вектор)
• 🎨 красить всё: эмодзи, контур, текст; темы и переливы
• 🧩 баннеры из нескольких частей: текст сам растягивается на все части
• 💎 / 🎭 складывать результат в твой эмодзи-пак или стикер-пак

<b>Как подготовить свой эмодзи</b>
• Холст 512×512, до 3 сек, итоговый .tgs ≤ 64 КБ
• Надпись — группа/слой из букв. Назови <code>надпись</code> или <code>text</code> (подойдут txt, trst, nick, HELL…) —
  если имя другое, найду по форме: ряд букв. Нашёл не то — «Тонкая настройка → Текст → Кандидаты»
• Внутри группы оставь заливку/контур — стиль сохранится
• Лого-фигуры назови <code>logo</code> — удалю автоматически, а если надписи нет — поставлю текст на их место
• Админ добавляет шаблоны: 🛠 → ➕ Добавить шаблон → кинь .json/.tgs"""


def guide_view():
    return GUIDE, [[("🏠 Меню", "h")]]


# ------------------------------------------------------------------ админка
def admin_main(stats):
    txt = (f"🛠 <b>Админ-панель</b>\n{LINE}\n👥 Пользователей: <b>{stats['users']}</b> (за сутки: {stats['day']})\n"
           f"🎞 Сборок: <b>{stats['renders']}</b>\n📦 Шаблонов: <b>{stats['tpls']}</b>\n"
           f"💾 Хранилище: <code>{esc(stats['path'])}</code>{'' if stats['persistent'] else chr(10) + '⚠️ Нет Volume — новые шаблоны пропадут при редеплое. Подключи Volume к /data'}")
    txt += f"\n🧾 Ждут оплаты: <b>{stats.get('pending', 0)}</b>"
    return txt, [[("👥 Пользователи и штуки", "a:users:0"), ("🧾 Заказы", "a:orders")],
                 [("📦 Шаблоны", "a:tpls:0"), ("➕ Добавить", "a:add")], [("🧩 Собрать баннер", "a:bn")],
                 [("📣 Рассылка", "a:bc"), ("🧪 Тест паков", "a:packtest")], [("🏠 Меню", "h")]]


def admin_tpls(idx, page):
    keys = list(idx)
    pages = max(1, (len(keys) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows = [[(f"{idx[k].get('emoji', '🎭')} {idx[k].get('title', k)}" + (" 🧩" if idx[k].get("parts") else "") + (" 🙈" if idx[k].get("hidden") else ""), f"a:t:{k}")]
            for k in keys[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0: nav.append(("◀️", f"a:tpls:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1: nav.append(("▶️", f"a:tpls:{page + 1}"))
    rows += [nav, [("➕ Добавить", "a:add"), ("◀️ Назад", "a:main")]]
    return f"📦 <b>Шаблоны</b> ({len(keys)})\n{LINE}\nВыбери, чтобы переименовать, поменять значок или удалить.", rows


def admin_tpl(key, meta, kb):
    st = meta.get("settings", {})
    kind = f"баннер из {len(meta['parts'])} частей" if meta.get("parts") else f"{kb} КБ"
    txt = (f"{meta.get('emoji', '🎭')} <b>{esc(meta.get('title', key))}</b>\n{LINE}\nКлюч: <code>{esc(key)}</code>\n"
           f"Тип: {kind}{' · 🙈 скрыт в каталоге' if meta.get('hidden') else ''}\n"
           f"Текстовый слой: <code>{esc(str(st.get('layer', 'auto')))}</code>")
    rows = [[("✏️ Название", f"a:ren:{key}"), ("😀 Значок", f"a:ico:{key}")],
            [("🧪 Тест", f"a:test:{key}"), (("👁 Показать" if meta.get("hidden") else "🙈 Скрыть"), f"a:hid:{key}")]]
    if not meta.get("parts"):
        rows.append([("🎯 Текстовый слой", f"a:ly:{key}")])
    rows += [[("🗑 Удалить", f"a:del:{key}")], [("◀️ К списку", "a:tpls:0")]]
    return txt, rows


def admin_confirm_delete(key, meta):
    return (f"🗑 Удалить «{esc(meta.get('title', key))}»? Это необратимо.",
            [[("✅ Да, удалить", f"a:delok:{key}"), ("↩️ Отмена", f"a:t:{key}")]])


def admin_layer(key, cands):
    rows = [[(f"{i + 1}. {str(c['name'] or 'без имени')[:18]} · {round(c['score'])}", f"a:lys:{key}:{i}")] for i, c in enumerate(cands)]
    rows.append([("◀️ Назад", f"a:t:{key}")])
    body = "\n".join(f"<b>{i + 1}.</b> {esc(c['path'])} — {esc(c['why'])}" for i, c in enumerate(cands)) or "Кандидатов нет."
    return f"🎯 <b>Текстовый слой</b>\n{LINE}\n{body}", rows


def admin_add():
    return (f"➕ <b>Добавление шаблонов</b>\n{LINE}\nКидай <code>.json</code> или <code>.tgs</code> — по одному или пачкой.\n"
            "Название = подпись к файлу (или имя файла). Текст найду сам.", [[("✅ Готово", "a:main")]])


def admin_banner(idx, parts, title):
    order = " → ".join(esc(idx[k]["title"]) for k in parts if k in idx) or "пока пусто"
    keys = [k for k in idx if not idx[k].get("parts")]
    rows, row = [], []
    for k in keys[:24]:
        row.append((f"{idx[k].get('emoji', '🎭')} {idx[k]['title']}"[:20], f"a:bp:{k}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([("↩️ Убрать последнюю", "a:bu"), ("💾 Сохранить", "a:bs")])
    rows.append([("◀️ Отмена", "a:main")])
    return (f"🧩 <b>Баннер «{esc(title)}»</b>\n{LINE}\nКлади части слева направо — тапай шаблоны по порядку.\n"
            f"Части: <b>{order}</b>\n\nТекст растянется на все части; каждая — отдельное эмодзи."), rows


# ---- админ: пользователи и штуки, заказы
def admin_users(users, page, per=8):
    ids = sorted(users, key=lambda k: -users[k].get("last", 0))
    pages = max(1, (len(ids) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    rows = [[(f"{users[k].get('name', k)[:16]} · {users[k].get('balance', 0)} шт · {k}", f"a:u:{k}")] for k in ids[page * per:(page + 1) * per]]
    nav = []
    if page > 0: nav.append(("◀️", f"a:users:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1: nav.append(("▶️", f"a:users:{page + 1}"))
    rows += [nav, [("🔎 Найти по ID", "a:uf")], [("◀️ Назад", "a:main")]]
    return f"👥 <b>Пользователи</b> ({len(ids)})\n{LINE}\nВыбери пользователя, чтобы выдать или снять штуки.", rows


def admin_user(uid, u):
    txt = (f"👤 <b>{esc(u.get('name', str(uid)))}</b>\n{LINE}\nID: <code>{uid}</code>\n"
           f"Баланс: <b>{u.get('balance', 0)} шт.</b>\nСобрано эмодзи: {u.get('renders', 0)}")
    rows = [[(f"➕ {n}", f"a:bal:{uid}:{n}") for n in (1, 5, 10, 50)],
            [(f"➖ {n}", f"a:bal:{uid}:-{n}") for n in (1, 5, 10, 50)],
            [("✍️ Своё число (+/−)", f"a:ubal:{uid}"), ("0️⃣ Обнулить", f"a:bal:{uid}:zero")],
            [("◀️ К списку", "a:users:0")]]
    return txt, rows


def admin_orders(orders):
    rows = [[(f"#{k} · {o['title']} · {o['amount']} · {o['uid']}", f"a:o:{k}")] for k, o in list(orders.items())[:10]]
    rows.append([("◀️ Назад", "a:main")])
    return f"🧾 <b>Заказы на проверку</b> ({len(orders)})\n{LINE}\n" + ("Нажми заказ, чтобы подтвердить." if orders else "Пока нет."), rows


def admin_order(oid, o):
    txt = (f"🧾 <b>Заказ #{oid}</b>\n{LINE}\nПользователь: <code>{o['uid']}</code>\n{esc(o['title'])} → +{o['units']} шт.\n"
           f"Сумма: <b>{esc(o['amount'])}</b>\nСтатус: {o['status']}")
    return txt, [[("✅ Подтвердить и начислить", f"a:ok:{oid}"), ("❌ Отклонить", f"a:no:{oid}")], [("◀️ К заказам", "a:orders")]]
