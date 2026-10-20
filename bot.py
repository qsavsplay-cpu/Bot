import asyncio
import copy
import io
import logging
import os
import re
import time
import traceback
from pathlib import Path

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputSticker, Update)
from telegram.constants import ParseMode, StickerType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, TypeHandler, filters)

import colors as C
import detect as D
import lottie_ops as O
import lottie_text as T
import pipeline as P
import store
import svgimport as SVG
import trace as TR
import ui

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("Не задана переменная BOT_TOKEN (Railway → Variables)")
ADMINS = {int(x) for x in re.split(r"[,\s]+", os.environ.get("ADMIN_ID", "")) if x.strip().lstrip("-").isdigit()}
FONTS = Path(__file__).parent / "fonts"
LIMIT = 64 * 1024
HTML = ParseMode.HTML
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("toxi")


# ---------------------------------------------------------------- helpers
def fonts_list():
    return sorted(p.name for p in FONTS.glob("*.[to]tf"))


def is_admin(u):
    return bool(u.effective_user and u.effective_user.id in ADMINS)


def markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=d) for l, d in r] for r in rows])


def S(ctx):
    ud = ctx.user_data
    s = ud.setdefault("s", copy.deepcopy(P.DEFAULTS))
    fl = fonts_list()
    if not s.get("font") or s["font"] not in fl:
        s["font"] = fl[0] if fl else None
    return s


def tpl_title(ud):
    return store.index().get(ud.get("tpl"), {}).get("title")


def own(ud):
    """Делает копию анимации приватной (шаблоны в кэше общие)."""
    if ud.get("shared"):
        ud["lottie"] = copy.deepcopy(ud["lottie"])
        ud["shared"] = False


async def show(u, ctx, view, new=False):
    text, rows = view
    kb = markup(rows)
    q = u.callback_query
    if q and not new:
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except TelegramError:
            pass
    await u.effective_message.reply_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)


def with_go(view):
    text, rows = view
    return text, rows[:-1] + [[("🚀 Собрать", "go")]] + rows[-1:]


def main(ctx, u, notice=None, found=None):
    ud = ctx.user_data
    return ui.main_view(ud, S(ctx), tpl_title(ud), notice, is_admin(u), found)


async def download(obj):
    buf = io.BytesIO()
    await (await obj.get_file()).download_to_memory(buf)
    return buf.getvalue()


def found_text(data, layer):
    if layer and layer != "auto":
        hits = []
        O.find_nodes(data, [layer], hits)
        if hits:
            return f"{layer}"
    c = D.find_candidates(data, 1)
    return f"{c[0]['name'] or 'без имени'} · {c[0]['why']}" if c else None


# ---------------------------------------------------------------- команды
async def track(u: Update, ctx):
    usr = u.effective_user
    if usr:
        store.touch_user(usr.id, usr.full_name)


async def start(u, ctx):
    await show(u, ctx, main(ctx, u, notice="👋 Привет! Я делаю <b>анимированные эмодзи с твоим текстом</b>: цвета, переливы, лого, премиум-пак."), new=True)


async def menu(u, ctx):
    await show(u, ctx, main(ctx, u), new=True)


async def templates_cmd(u, ctx):
    await show(u, ctx, ui.templates_view(store.index(), 0), new=True)


async def guide_cmd(u, ctx):
    await show(u, ctx, ui.guide_view(), new=True)


async def admin_cmd(u, ctx):
    if not is_admin(u):
        return await u.effective_message.reply_text("⛔️ Только для админа. Твой ID: <code>%d</code>" % u.effective_user.id, parse_mode=HTML)
    await show(u, ctx, admin_main_view(), new=True)


async def tree_cmd(u, ctx):
    if not ctx.user_data.get("lottie"):
        return await u.effective_message.reply_text("Сначала выбери эмодзи")
    txt = O.tree(ctx.user_data["lottie"])
    await u.effective_message.reply_document(io.BytesIO(txt.encode()), filename="tree.txt",
                                             caption="🌲 Слои и папки (имена для масок удаления)")


async def pack_cmd(u, ctx):
    await make_pack(u, ctx)


# ---------------------------------------------------------------- сборка
def build(ud, s, text):
    font = FONTS / s["font"]
    logo = ud.get("logo")
    tries = [(s["img_grid"], s["img_colors"])]
    if logo and logo["kind"] == "img":
        tries += [(min(s["img_grid"], 56), min(s["img_colors"], 6)), (min(s["img_grid"], 40), min(s["img_colors"], 5))]
    for grid, cols in tries:
        data, rep = P.render(ud["lottie"], s | {"img_grid": grid, "img_colors": cols}, text, font, logo)
        tgs, lvl = T.fit_tgs(data, LIMIT)
        if len(tgs) <= LIMIT:
            break
    return tgs, rep, lvl


async def send_sticker(msg, tgs, name="emoji.tgs"):
    try:
        return await msg.reply_sticker(InputFile(io.BytesIO(tgs), filename=name))
    except TelegramError:
        return await msg.reply_document(io.BytesIO(tgs), filename=name)


async def do_render(u, ctx, text):
    ud, s = ctx.user_data, S(ctx)
    m = u.effective_message
    if not ud.get("lottie"):
        return await show(u, ctx, ui.templates_view(store.index(), 0), new=True)
    if not s["font"]:
        return await m.reply_text("Нет шрифтов в папке fonts/")
    ud["last_text"] = text
    wait = await m.reply_text("⏳ <i>Собираю эмодзи…</i>", parse_mode=HTML)
    try:
        tgs, rep, lvl = await asyncio.to_thread(build, ud, s, text)
    except Exception as e:
        log.exception("render")
        await wait.edit_text(f"😵 Не получилось собрать: <code>{ui.esc(str(e))}</code>", parse_mode=HTML)
        return
    await wait.delete()
    if not rep["text"]:
        return await show(u, ctx, ui.candidates_view(own_cands(ud)), new=True)
    ud["last_tgs"] = tgs
    store.count_render(u.effective_user.id)
    await send_sticker(m, tgs)
    note = [f"✅ <b>Готово</b> · {len(tgs) // 1024 or 1} КБ"]
    if rep["removed"]: note.append(f"🧹 удалено: {rep['removed']}")
    if rep["recolored"]: note.append(f"🎨 перекрашено: {rep['recolored']}")
    if ud.get("logo"): note.append("🖼 лого добавлено" if rep["logo"] else "🖼 лого не встало (проверь положение)")
    if lvl: note.append("🗜 сжато до лимита Telegram")
    if len(tgs) > LIMIT: note.append(f"⚠️ {len(tgs) // 1024} КБ — больше лимита 64 КБ, упрости цвета/лого")
    await show(u, ctx, main(ctx, u, notice=" · ".join(note)), new=True)


def own_cands(ud):
    own(ud)
    ud["cands"] = D.find_candidates(ud["lottie"], 6)
    return ud["cands"]


# ---------------------------------------------------------------- премиум-пак
async def make_pack(u, ctx):
    ud = ctx.user_data
    m = u.effective_message
    if not ud.get("last_tgs"):
        return await m.reply_text("Сначала собери эмодзи — напиши текст 🙂")
    me = await ctx.bot.get_me()
    uid = u.effective_user.id
    name = f"toxi{uid}_by_{me.username}"
    title = f"TOXI · {u.effective_user.first_name}"[:64]
    st = InputSticker(sticker=InputFile(io.BytesIO(ud["last_tgs"]), filename="emoji.tgs"),
                      emoji_list=["✨"], format="animated")
    try:
        try:
            await ctx.bot.get_sticker_set(name)
            await ctx.bot.add_sticker_to_set(user_id=uid, name=name, sticker=st)
        except BadRequest as e:
            if "invalid" not in str(e).lower() and "not found" not in str(e).lower() and "STICKERSET" not in str(e).upper():
                raise
            await ctx.bot.create_new_sticker_set(user_id=uid, name=name, title=title, stickers=[st],
                                                 sticker_type=StickerType.CUSTOM_EMOJI)
    except TelegramError as e:
        log.warning("pack failed: %s", e)
        return await m.reply_text(f"😕 Не получилось добавить в пак: <code>{ui.esc(str(e))}</code>\n"
                                  "Если пишет про размер/формат — упрости эмодзи (меньше цветов/лого).", parse_mode=HTML)
    await m.reply_text(f"💎 <b>Добавлено в твой пак премиум-эмодзи!</b>\n"
                       f"<a href=\"https://t.me/addemoji/{name}\">Открыть и установить пак</a>\n"
                       "Пользоваться ими смогут обладатели Telegram Premium.", parse_mode=HTML)


# ---------------------------------------------------------------- файлы
async def on_file(u, ctx):
    m, ud = u.message, ctx.user_data
    kind, obj = None, None
    if m.sticker:
        st = m.sticker
        if st.is_animated:
            kind, obj = "anim", st
        elif st.is_video:
            return await m.reply_text("Видео-стикеры (webm) не поддерживаю — пришли .tgs или картинку.")
        else:
            kind, obj = "img", st
    elif m.photo:
        kind, obj = "img", m.photo[-1]
    elif m.document:
        nm = (m.document.file_name or "").lower()
        mime = m.document.mime_type or ""
        obj = m.document
        if nm.endswith((".json", ".tgs")): kind = "anim"
        elif nm.endswith(".svg"): kind = "svg"
        elif mime.startswith("image/") or nm.endswith((".png", ".jpg", ".jpeg", ".webp")): kind = "img"
    if not kind:
        return await m.reply_text("Понимаю: <code>.json</code> / <code>.tgs</code> (эмодзи), <code>.svg</code>, PNG/JPG/WEBP (лого).", parse_mode=HTML)
    data = await download(obj)

    if kind == "anim":
        if is_admin(u) and ud.get("await") == ("tpladd",):
            return await admin_add_file(u, ctx, data, (m.caption or "").strip() or Path(getattr(obj, "file_name", None) or "эмодзи").stem)
        try:
            lot = T.load(data)
            assert "layers" in lot
        except Exception as e:
            return await m.reply_text(f"Не смог прочитать анимацию: {e}")
        ud.update(lottie=lot, tpl=None, shared=False)
        ud["s"] = copy.deepcopy(P.DEFAULTS)
        found = found_text(lot, "auto")
        if m.caption:
            return await do_render(u, ctx, m.caption)
        ud["await"] = None
        return await show(u, ctx, main(ctx, u, notice="📥 Файл принят", found=found or "не нашёл — выбери в ✏️ Текст → 🧭 Кандидаты"), new=True)

    if kind == "svg":
        try:
            await asyncio.to_thread(SVG.svg_to_group, data, 90)
        except Exception as e:
            return await m.reply_text(f"Не получилось прочитать SVG: {e}")
        ud["logo"] = {"kind": "svg", "data": data}
    else:
        try:
            await asyncio.to_thread(TR.image_to_group, data, 90, S(ctx)["img_colors"], S(ctx)["img_grid"], None, S(ctx)["img_bg"])
        except Exception as e:
            return await m.reply_text(f"Не получилось превратить картинку в вектор: {e}")
        ud["logo"] = {"kind": "img", "data": data}
    await show(u, ctx, with_go(ui.logo_view(ud, S(ctx))), new=True)
    await m.reply_text("✅ Лого принято. Подправь положение/размер и жми 🚀 Собрать")


async def on_text(u, ctx):
    ud, text = ctx.user_data, u.message.text.strip()
    aw = ud.get("await")
    if aw and aw[0] != "tpladd":
        ud["await"] = None
        return await handle_await(u, ctx, aw, text)
    if aw and aw[0] == "tpladd":
        return await u.message.reply_text("Жду файл .json / .tgs 📎")
    await do_render(u, ctx, text)


def apply_spec(s, target, spec, only_text_none=True):
    keys = ("fill", "stroke", "text_fill", "text_stroke") if target == "all" else (target,)
    for k in keys:
        if spec is None or spec["mode"] == "reset":
            s[k] = None
        elif spec["mode"] == "none":
            if k in ("text_fill", "text_stroke"):
                s[k] = spec
        else:
            s[k] = spec


async def handle_await(u, ctx, aw, text):
    ud, s, m = ctx.user_data, S(ctx), u.effective_message
    kind = aw[0]
    try:
        if kind == "spec":
            spec = C.parse_spec(text)
            if aw[1] in ("fill", "stroke") and spec["mode"] == "none":
                return await m.reply_text("У эмодзи нельзя убрать заливку/контур. Сброс: off")
            apply_spec(s, aw[1], spec)
            return await show(u, ctx, with_go(ui.colors_view(ud, s)), new=True)
        if kind == "logocolor":
            spec = C.parse_spec(text)
            s["logo_color"] = None if spec["mode"] in ("reset", "none") else {"mode": "static", "colors": spec["colors"][:1]}
            return await show(u, ctx, with_go(ui.logo_view(ud, s)), new=True)
        if kind == "layer":
            s["layer"] = text
            return await show(u, ctx, with_go(ui.text_view(s, fonts_list())), new=True)
        if kind == "remove":
            s["remove"] = [x for x in text.split() if x][:30]
            return await show(u, ctx, ui.remove_view(s), new=True)
        if kind == "skip":
            s["skip"] = [x for x in text.split() if x][:30]
            return await show(u, ctx, ui.remove_view(s), new=True)
        if kind == "tplname" and is_admin(u):
            store.update_template(aw[1], title=text[:40])
            return await show(u, ctx, admin_tpl_view(aw[1]), new=True)
        if kind == "bc" and is_admin(u):
            return await broadcast(u, ctx, text)
    except ValueError as e:
        ud["await"] = aw
        await m.reply_text(f"⚠️ {e}")


# ---------------------------------------------------------------- админка
def admin_main_view():
    us = store.users()
    now = time.time()
    stats = {"users": len(us), "day": sum(1 for x in us.values() if now - x.get("last", 0) < 86400),
             "renders": sum(x.get("renders", 0) for x in us.values()), "tpls": len(store.index()),
             "path": str(store.DATA), "persistent": str(store.DATA) != str(store.BASE / "data")}
    return ui.admin_main(stats)


def admin_tpl_view(key):
    meta = store.index().get(key, {})
    p = store.TPL / meta.get("file", "")
    return ui.admin_tpl(key, meta, (p.stat().st_size // 1024) if p.exists() else 0)


async def admin_add_file(u, ctx, data, title):
    m = u.effective_message
    try:
        lot = T.load(data)
        assert "layers" in lot
    except Exception as e:
        return await m.reply_text(f"❌ Не смог прочитать: {e}")
    layer = D.bake(lot)
    key = store.new_key("t")
    store.add_template(key, title[:40], lot, {"layer": layer})
    kb = (store.TPL / f"{key}.tgs").stat().st_size // 1024
    warn = f"\n⚠️ {kb} КБ — впритык к лимиту 64 КБ" if kb > 56 else ""
    c = D.find_candidates(lot, 1)
    info = f"найден: {c[0]['why']}" if c else "текст не найден — выбери вручную (🎯)"
    await m.reply_text(f"✅ Шаблон <b>{ui.esc(title)}</b> добавлен · {kb} КБ{warn}\n🔎 {ui.esc(info)}\nМожно кидать следующий 📎",
                       parse_mode=HTML, reply_markup=markup([[("⚙️ Управлять", f"a:t:{key}"), ("✅ Готово", "a:main")]]))


async def broadcast(u, ctx, text):
    ok = bad = 0
    for uid in list(store.users()):
        try:
            await ctx.bot.send_message(int(uid), text)
            ok += 1
        except TelegramError:
            bad += 1
        await asyncio.sleep(0.05)
    await u.effective_message.reply_text(f"📣 Отправлено: {ok} · не доставлено: {bad}")


async def admin_cb(u, ctx, q, parts):
    ud = ctx.user_data
    act = parts[1]
    if act == "main":
        ud["await"] = None
        return await show(u, ctx, admin_main_view())
    if act == "tpls":
        return await show(u, ctx, ui.admin_tpls(store.index(), int(parts[2])))
    if act == "add":
        ud["await"] = ("tpladd",)
        return await show(u, ctx, ui.admin_add())
    if act == "bc":
        ud["await"] = ("bc",)
        return await q.message.reply_text("📣 Пришли текст рассылки одним сообщением")
    key = parts[2] if len(parts) > 2 else None
    if act == "t":
        return await show(u, ctx, admin_tpl_view(key))
    if act == "ren":
        ud["await"] = ("tplname", key)
        return await q.message.reply_text("✏️ Пришли новое название")
    if act == "del":
        return await show(u, ctx, ui.admin_confirm_delete(key, store.index().get(key, {})))
    if act == "delok":
        store.delete_template(key)
        return await show(u, ctx, ui.admin_tpls(store.index(), 0))
    if act == "ly":
        lot = store.load_template(key)
        ud["admin_cands"] = (key, D.find_candidates(lot, 6))
        return await show(u, ctx, ui.admin_layer(key, ud["admin_cands"][1]))
    if act == "lys":
        k, cands = ud.get("admin_cands", (None, []))
        i = int(parts[3])
        if k != key or i >= len(cands):
            return
        lot = copy.deepcopy(store.load_template(key))
        cands = D.find_candidates(lot, 6)
        D.set_text_node(lot, cands[i])
        layer = D.bake_node(lot, cands[i])
        meta = store.index()[key]
        store.add_template(key, meta["title"], lot, {**meta.get("settings", {}), "layer": layer}, meta.get("emoji", "🎭"))
        store.update_template(key, order=meta.get("order", 999))
        return await show(u, ctx, admin_tpl_view(key))
    if act == "test":
        lot = store.load_template(key)
        s = copy.deepcopy(P.DEFAULTS) | store.index()[key].get("settings", {})
        s["font"] = fonts_list()[0]
        data, rep = await asyncio.to_thread(P.render, lot, s, "TEST", FONTS / s["font"], None)
        tgs, _ = T.fit_tgs(data, LIMIT)
        await send_sticker(q.message, tgs)
        return await q.message.reply_text(f"🧪 Тест: текст {'✅ заменён' if rep['text'] else '❌ не найден'} · {len(tgs) // 1024} КБ")


# ---------------------------------------------------------------- кнопки
ALERTY = {"a", "t", "tgs", "cn", "ly"}


async def on_cb(u, ctx):
    q = u.callback_query
    done = []

    async def ack(text=None, alert=False):
        if not done:
            done.append(1)
            try:
                await q.answer(text, show_alert=alert)
            except TelegramError:
                pass

    if q.data.split(":")[0] not in ALERTY:
        await ack()
    try:
        await _on_cb(u, ctx, ack)
    finally:
        await ack()


async def _on_cb(u, ctx, ack):
    q = u.callback_query
    d = q.data
    ud, s = ctx.user_data, S(ctx)
    parts = d.split(":")
    cmd = parts[0]
    if cmd == "noop":
        return

    if cmd == "a":
        if not is_admin(u):
            return await ack("Только для админа", True)
        return await admin_cb(u, ctx, q, parts)

    if cmd == "m":
        view = parts[1]
        ud["await"] = None
        if view == "main": return await show(u, ctx, main(ctx, u))
        if view == "tpl": return await show(u, ctx, ui.templates_view(store.index(), int(parts[2])))
        if view == "txt": return await show(u, ctx, with_go(ui.text_view(s, fonts_list())))
        if view == "col": return await show(u, ctx, with_go(ui.colors_view(ud, s)))
        if view == "logo": return await show(u, ctx, with_go(ui.logo_view(ud, s)))
        if view == "rm": return await show(u, ctx, ui.remove_view(s))
        if view == "set": return await show(u, ctx, ui.settings_view(ud, s))
        if view == "guide": return await show(u, ctx, ui.guide_view())

    if cmd == "t":                                           # выбор шаблона
        key = d[2:]
        meta = store.index().get(key)
        if not meta:
            return await ack("Шаблон не найден", True)
        ud.update(lottie=store.load_template(key), tpl=key, shared=True)
        ud["s"] = copy.deepcopy(P.DEFAULTS) | copy.deepcopy(meta.get("settings", {}))
        ud["await"] = None
        try:
            await send_sticker(q.message, (store.TPL / meta["file"]).read_bytes())
        except Exception:
            pass
        return await show(u, ctx, main(ctx, u, notice="✏️ <b>Напиши текст</b> — и я заменю надпись",
                                       found=found_text(ud["lottie"], s_layer(ud))), new=True)

    if cmd == "go":
        if not ud.get("last_text"):
            ud["await"] = None
            return await q.message.reply_text("✏️ Напиши текст сообщением")
        return await do_render(u, ctx, ud["last_text"])
    if cmd == "tgs":
        if not ud.get("last_tgs"):
            return await ack("Сначала собери эмодзи", True)
        return await q.message.reply_document(io.BytesIO(ud["last_tgs"]), filename="emoji.tgs", caption="📎 .tgs — можно добавить в пак стикеров/эмодзи")
    if cmd == "pack":
        return await make_pack(u, ctx)
    if cmd == "reset":
        meta = store.index().get(ud.get("tpl"), {})
        ud["s"] = copy.deepcopy(P.DEFAULTS) | copy.deepcopy(meta.get("settings", {}))
        ud["logo"] = None
        return await show(u, ctx, main(ctx, u, notice="♻️ Настройки сброшены"))
    if cmd == "tree":
        return await tree_cmd(u, ctx)

    # --- цвета
    if cmd == "c":
        ud["target"] = parts[1]
        return await show(u, ctx, with_go(ui.colors_view(ud, s)))
    if cmd in ("cs", "cp", "cn", "cr", "cm"):
        t = ud.get("target", "all")
        if cmd == "cm":
            ud["fxmode"] = "cycle" if ud.get("fxmode", "grad") == "grad" else "grad"
        elif cmd == "cs":
            apply_spec(s, t, {"mode": "static", "colors": [C.parse_color(ui.SWATCH[int(parts[1])][1])]})
        elif cmd == "cp":
            hexes = ui.PRESETS[parts[1]][2]
            apply_spec(s, t, C.parse_spec(("grad " if ud.get("fxmode", "grad") == "grad" else "") + hexes))
        elif cmd == "cr":
            apply_spec(s, t, None)
        elif cmd == "cn":
            if t in ("fill", "stroke"):
                return await ack("У эмодзи заливку/контур убрать нельзя", True)
            apply_spec(s, t, {"mode": "none", "colors": []})
        return await show(u, ctx, with_go(ui.colors_view(ud, s)))
    if cmd == "cu":
        ud["await"] = ("spec", ud.get("target", "all"))
        return await q.message.reply_text(
            "✍️ Пришли цвета:\n<code>#ff0000</code> — один\n<code>#ff0000 #0000ff</code> — смена\n"
            "<code>grad #f00 #ff0 #0f0</code> — перелив\n<code>off</code> — сброс", parse_mode=HTML)

    # --- числа
    if cmd == "n":
        step, lo, hi, _ = ui.NUMS[parts[1]]
        cur = s.get(parts[1]) or 0
        s[parts[1]] = max(lo, min(hi, cur + (step if parts[2] == "+" else -step)))
        back = {"size": "txt", "stroke_w": "txt", "cycles": "col"}.get(parts[1], "logo")
        view = {"txt": lambda: ui.text_view(s, fonts_list()), "col": lambda: ui.colors_view(ud, s), "logo": lambda: ui.logo_view(ud, s)}[back]()
        return await show(u, ctx, with_go(view))
    if cmd == "w":
        if parts[1] == "cycle":
            s["width"] = None if s["width"] == 0 else (0 if s["width"] is None else None)
        else:
            s["width"] = max(20, min(1000, (s["width"] or 300) + (20 if parts[1] == "+" else -20)))
        return await show(u, ctx, with_go(ui.text_view(s, fonts_list())))
    if cmd == "f":
        fl = fonts_list()
        if fl:
            s["font"] = fl[(fl.index(s["font"]) + 1) % len(fl)] if s["font"] in fl else fl[0]
        return await show(u, ctx, with_go(ui.text_view(s, fl)))

    # --- слой с текстом
    if cmd == "ly":
        if not ud.get("lottie"):
            return await ack("Сначала выбери эмодзи", True)
        a = parts[1]
        if a == "auto":
            s["layer"] = "auto"
            return await show(u, ctx, with_go(ui.text_view(s, fonts_list())))
        if a == "cand":
            return await show(u, ctx, ui.candidates_view(own_cands(ud)))
        if a == "in":
            ud["await"] = ("layer",)
            return await q.message.reply_text("✍️ Пришли имя слоя/папки с текстом (см. 🌲 Структура файла)")
        cands = ud.get("cands") or []
        i = int(a)
        if i < len(cands):
            own(ud)
            D.set_text_node(ud["lottie"], cands[i])
            s["layer"] = D.bake_node(ud["lottie"], cands[i])
        return await show(u, ctx, with_go(ui.text_view(s, fonts_list())))

    # --- лого
    if cmd == "l":
        s["logo_pos"] = parts[1]
        s["logo_dx"] = s["logo_dy"] = 0
        return await show(u, ctx, with_go(ui.logo_view(ud, s)))
    if cmd == "lb":
        s["img_bg"] = not s["img_bg"]
        return await show(u, ctx, with_go(ui.logo_view(ud, s)))
    if cmd == "lr":
        ud["logo"] = None
        return await show(u, ctx, with_go(ui.logo_view(ud, s)))
    if cmd == "lc":
        ud["await"] = ("logocolor",)
        return await q.message.reply_text("🎨 Пришли цвет лого, например <code>#ffffff</code> (или off — свои цвета)", parse_mode=HTML)

    # --- удаление
    if cmd == "al":
        s["autologo"] = not s["autologo"]
        return await show(u, ctx, ui.remove_view(s))
    if cmd == "rc":
        s["remove"], s["skip"] = [], []
        return await show(u, ctx, ui.remove_view(s))
    if cmd == "ri":
        ud["await"] = ("remove",)
        return await q.message.reply_text("🧹 Пришли маски через пробел, например: <code>logo* ruka*</code>", parse_mode=HTML)
    if cmd == "si":
        ud["await"] = ("skip",)
        return await q.message.reply_text("🚫 Пришли маски, которые НЕ красить, через пробел: <code>eye* блик*</code>", parse_mode=HTML)


def s_layer(ud):
    return ud.get("s", {}).get("layer", "auto")


async def on_error(u, ctx):
    log.error("update failed", exc_info=ctx.error)
    tb = "".join(traceback.format_exception(None, ctx.error, ctx.error.__traceback__))[-900:]
    try:
        if isinstance(u, Update) and u.effective_message:
            await u.effective_message.reply_text("😵 Что-то пошло не так. Попробуй ещё раз или /menu")
        for a in ADMINS:
            await ctx.bot.send_message(a, f"⚠️ Ошибка:\n<pre>{ui.esc(tb)}</pre>", parse_mode=HTML)
    except Exception:
        pass


async def post_init(app):
    store.init()
    from telegram import BotCommand
    await app.bot.set_my_commands([BotCommand("menu", "🏠 Главное меню"), BotCommand("templates", "🎭 Выбрать эмодзи"),
                                   BotCommand("guide", "📖 Гайд"), BotCommand("pack", "💎 В премиум-пак"),
                                   BotCommand("admin", "🛠 Админ-панель")])
    log.info("Bot ready. DATA=%s admins=%s templates=%d", store.DATA, ADMINS, len(store.index()))


def main_app():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, track), group=-1)
    for name, fn in {"start": start, "menu": menu, "help": guide_cmd, "guide": guide_cmd, "templates": templates_cmd,
                     "admin": admin_cmd, "tree": tree_cmd, "pack": pack_cmd}.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.Sticker.ALL | filters.PHOTO, on_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main_app()
