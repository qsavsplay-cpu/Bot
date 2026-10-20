import asyncio
import copy
import io
import logging
import os
import re
import time
import traceback
from pathlib import Path

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
                      InputMediaAnimation, InputSticker, Update)
from telegram.constants import ParseMode, StickerType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, TypeHandler, filters)

import colors as C
import detect as D
import lottie_ops as O
import lottie_text as T
import pipeline as P
import preview
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
MAX_JOBS = 16
HTML = ParseMode.HTML
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("toxi")
_pages = {}        # кэш GIF-страниц каталога: ключ -> (bytes, file_id)


# ================================================================ утилиты
def fonts_list():
    return sorted(p.name for p in FONTS.glob("*.[to]tf"))


def is_admin(u):
    return bool(u.effective_user and u.effective_user.id in ADMINS)


def markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=d) for l, d in r] for r in rows])


def S(ctx):
    s = ctx.user_data.setdefault("s", copy.deepcopy(P.DEFAULTS))
    for k, v in P.DEFAULTS.items():
        s.setdefault(k, copy.deepcopy(v))
    fl = fonts_list()
    if not s.get("font") or s["font"] not in fl:
        s["font"] = fl[0] if fl else None
    return s


def own(ud):
    if ud.get("shared"):
        ud["lottie"] = copy.deepcopy(ud["lottie"])
        ud["shared"] = False


async def show(u, ctx, view, new=False):
    text, rows = view[0], view[1]
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
        try:                                     # сообщение-медиа нельзя превратить в текст: убираем и шлём новое
            await q.message.delete()
        except TelegramError:
            pass
    await u.effective_message.chat.send_message(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)


async def download(obj):
    buf = io.BytesIO()
    await (await obj.get_file()).download_to_memory(buf)
    return buf.getvalue()


def get_lot(ud, key):
    if key.startswith("u") and ud.get("own_tpl") is not None and key not in store.index():
        return ud["own_tpl"]
    return store.load_template(key)


def sel_titles(ud):
    idx = store.index()
    return [idx[k]["title"] for k in ud.get("sel", []) if k in idx]


def apply_theme(s, tid):
    val = ui.THEMES[tid][2]
    keys = ("fill", "stroke", "text_fill", "text_stroke")
    if val is None:
        for k in keys:
            s[k] = None
    elif isinstance(val, str):
        spec = C.parse_spec("grad " + ui.PRESETS[val[7:]][2])
        for k in keys:
            s[k] = spec
    else:
        for k, v in zip(keys, val):
            s[k] = {"mode": "static", "colors": [C.parse_color(v)]}


def apply_spec(s, target, spec):
    keys = ("fill", "stroke", "text_fill", "text_stroke") if target == "all" else (target,)
    for k in keys:
        if spec is None or spec["mode"] == "reset":
            s[k] = None
        elif spec["mode"] == "none":
            if k in ("text_fill", "text_stroke"):
                s[k] = spec
        else:
            s[k] = spec


def part_settings(s, key):
    ms = store.index().get(key, {}).get("settings", {})
    out = copy.deepcopy(s)
    if s["layer"] == "auto" and ms.get("layer"):
        out["layer"] = ms["layer"]
    out["skip"] = list(dict.fromkeys(list(s["skip"]) + list(ms.get("skip", []))))
    return out


# ================================================================ главная / каталог
async def track(u: Update, ctx):
    if u.effective_user:
        store.touch_user(u.effective_user.id, u.effective_user.full_name)


def home(ctx, u, notice=None):
    return ui.home_view(ctx.user_data, S(ctx), is_admin(u), notice)


async def start(u, ctx):
    ctx.user_data["await"] = None
    await show(u, ctx, home(ctx, u, "👋 Привет! Сделаем анимированное эмодзи?"), new=True)


async def menu(u, ctx):
    ctx.user_data["await"] = None
    await show(u, ctx, home(ctx, u), new=True)


async def guide_cmd(u, ctx):
    await show(u, ctx, ui.guide_view(), new=True)


async def admin_cmd(u, ctx):
    if not is_admin(u):
        return await u.effective_message.reply_text(f"⛔️ Только для админа. Твой ID: <code>{u.effective_user.id}</code>", parse_mode=HTML)
    await show(u, ctx, admin_main_view(), new=True)


async def tree_cmd(u, ctx):
    ud = ctx.user_data
    lot = ud.get("own_tpl") or (store.load_template(store.expand(ud["sel"][0])[0]) if ud.get("sel") else None)
    if not lot:
        return await u.effective_message.reply_text("Сначала выбери эмодзи")
    await u.effective_message.reply_document(io.BytesIO(O.tree(lot).encode()), filename="tree.txt",
                                             caption="🌲 Слои и папки (имена для масок удаления)")


async def new_cmd(u, ctx):
    await open_catalog(u, ctx, 0, fresh=True)


async def open_catalog(u, ctx, page, fresh=False):
    """Каталог: GIF-коллаж страницы с названиями. При fresh — сбрасывает выбор."""
    ud = ctx.user_data
    if fresh:
        ud["sel"], ud["await"], ud["stage"] = [], None, "catalog"
    ud.setdefault("sel", [])
    idx = store.visible()
    if not idx:
        return await show(u, ctx, ("Каталог пуст — админ ещё не добавил эмодзи.", [[("🏠 Меню", "h")]]), new=True)
    cap, rows, page, chunk = ui.catalog_view(idx, page, ud["sel"], sel_titles(ud))
    ud["page"], ud["chunk"] = page, chunk
    items = [(k, idx[k]["title"]) for k in chunk]
    ck = tuple((k, t, store.index()[k].get("emoji")) for k, t in items) + (tuple(store.index()[k].get("file", "") for k in chunk),)
    q = u.callback_query
    gif = None
    if ck in _pages:
        gif, fid = _pages[ck]
    else:
        note = None
        if q:
            await q.answer("⏳ Рисую превью…")
        else:
            note = await u.effective_message.reply_text("⏳ <i>Рисую превью каталога…</i>", parse_mode=HTML)
        try:
            gif = await asyncio.to_thread(preview.page_gif, items, page * ui.PAGE + 1)
            fid = None
            _pages[ck] = (gif, None)
            if len(_pages) > 24:
                _pages.pop(next(iter(_pages)))
        except Exception:
            log.exception("preview failed")
            gif = None
        if note:
            try:
                await note.delete()
            except TelegramError:
                pass
    kb = markup(rows)
    if gif is None:                                              # превью не вышло — текстовый каталог
        return await show(u, ctx, (cap, rows), new=bool(not q))
    media_src = _pages[ck][1] or InputFile(io.BytesIO(gif), filename="catalog.gif")
    try:
        if q and q.message and q.message.animation:
            m = await q.edit_message_media(InputMediaAnimation(media=media_src, caption=cap, parse_mode=HTML), reply_markup=kb)
        else:
            if q:
                try:
                    await q.message.delete()
                except TelegramError:
                    pass
            m = await u.effective_message.chat.send_animation(media_src, caption=cap, parse_mode=HTML, reply_markup=kb)
        if getattr(m, "animation", None):
            _pages[ck] = (gif, m.animation.file_id)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            _pages.pop(ck, None)
            raise


async def edit_caption(u, cap, rows):
    q = u.callback_query
    try:
        await q.edit_message_caption(caption=cap, parse_mode=HTML, reply_markup=markup(rows))
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ================================================================ сборка
def build_one(lottie, s, text, logo, part):
    font = FONTS / s["font"]
    tries = [(s["img_grid"], s["img_colors"])]
    if logo and logo["kind"] == "img":
        tries += [(min(s["img_grid"], 56), min(s["img_colors"], 6)), (min(s["img_grid"], 40), min(s["img_colors"], 5))]
    for grid, cols in tries:
        data, rep = P.render(lottie, s | {"img_grid": grid, "img_colors": cols}, text, font, logo, part)
        tgs, lvl = T.fit_tgs(data, LIMIT)
        if len(tgs) <= LIMIT:
            break
    return tgs, rep, lvl


async def send_sticker(chat, tgs, name="emoji.tgs"):
    try:
        return await chat.send_sticker(InputFile(io.BytesIO(tgs), filename=name))
    except TelegramError:
        return await chat.send_document(InputFile(io.BytesIO(tgs), filename=name))


async def ask_dest(u, ctx, lines):
    ud, s = ctx.user_data, S(ctx)
    ud["pending"] = {"lines": lines}
    idx = store.index()
    per = sum(len(store.expand(k)) for k in ud["sel"])
    n_lines = len(lines) if s["mode"] == "text" else 1
    total = per * n_lines
    await show(u, ctx, ui.dest_view(len(ud["sel"]) * n_lines, lines, s["mode"], total), new=True)


async def run_jobs(u, ctx, dest):
    ud, s = ctx.user_data, S(ctx)
    chat = u.effective_chat
    pend = ud.pop("pending", None)
    if not pend or not ud.get("sel"):
        return await show(u, ctx, home(ctx, u, "Сначала выбери эмодзи и текст"), new=True)
    if not s["font"]:
        return await chat.send_message("Нет шрифтов в папке fonts/")
    mode = s["mode"]
    if mode == "logo" and not ud.get("logo"):
        return await chat.send_message("Сначала пришли лого 🖼")
    lines = pend["lines"] if mode == "text" else [""]
    jobs = []
    for key in ud["sel"]:
        parts = store.expand(key)
        for line in lines:
            for i, pk in enumerate(parts):
                jobs.append((key, pk, line, (i, len(parts)) if len(parts) > 1 else None))
    skipped = max(0, len(jobs) - MAX_JOBS)
    jobs = jobs[:MAX_JOBS]
    wait = await chat.send_message(f"⏳ <b>Собираю</b> 0/{len(jobs)}…", parse_mode=HTML)
    results, errors, notes = [], 0, set()
    for n, (key, pk, line, part) in enumerate(jobs, 1):
        meta = store.index().get(pk, {})
        try:
            lot = get_lot(ud, pk)
            tgs, rep, lvl = await asyncio.to_thread(build_one, lot, part_settings(s, pk), line, ud.get("logo"), part)
            if rep.get("fallback"):
                notes.add("в одном шаблоне не было места под надпись — добавил её отдельным слоем")
            if lvl:
                notes.add("🗜 часть файлов сжата под лимит Telegram")
            if len(tgs) > LIMIT:
                notes.add(f"⚠️ «{meta.get('title', pk)}»: {len(tgs) // 1024} КБ — больше лимита 64 КБ")
            results.append((tgs, store.index().get(key, {}).get("emoji", "✨")))
            store.count_render(u.effective_user.id)
        except Exception:
            log.exception("job failed")
            errors += 1
        try:
            await wait.edit_text(f"⏳ <b>Собираю</b> {n}/{len(jobs)}…", parse_mode=HTML)
        except TelegramError:
            pass
    try:
        await wait.delete()
    except TelegramError:
        pass
    if not results:
        return await chat.send_message("😵 Ничего не собралось. Попробуй другой шаблон или /menu")
    ud["last_tgs"] = results[-1][0]
    link = None
    if dest == "send":
        for tgs, _ in results:
            await send_sticker(chat, tgs)
            await asyncio.sleep(0.25)
    else:
        try:
            link = await add_to_pack(ctx, u.effective_user, dest, results)
        except TelegramError as e:
            log.warning("pack failed: %s", e)
            notes.add(f"💎 не удалось добавить в пак: {ui.esc(str(e))} — отправляю файлами")
            for tgs, _ in results:
                await send_sticker(chat, tgs)
                await asyncio.sleep(0.25)
    note = [f"✅ <b>Готово: {len(results)} шт.</b>"]
    if link:
        label = "эмодзи-пак" if dest == "emoji" else "стикер-пак"
        note.append(f"{'💎' if dest == 'emoji' else '🎭'} <a href=\"{link}\">Открыть {label}</a>")
        if dest == "emoji":
            note.append("<i>Использовать эмодзи из пака смогут обладатели Premium.</i>")
    if errors:
        note.append(f"❗️ не собралось: {errors}")
    if skipped:
        note.append(f"✂️ лимит {MAX_JOBS} за раз — лишнее пропущено ({skipped})")
    note += sorted(notes)
    ud["stage"] = "done"
    await show(u, ctx, ui.done_view(s, "\n".join(note)), new=True)


# ================================================================ паки
def pack_names(me_username, uid):
    return {"emoji": f"toxi{uid}_by_{me_username}", "sticker": f"toxs{uid}_by_{me_username}"}


async def add_to_pack(ctx, user, kind, items):
    me = await ctx.bot.get_me()
    name = pack_names(me.username, user.id)[kind]
    stype = StickerType.CUSTOM_EMOJI if kind == "emoji" else StickerType.REGULAR
    title = f"TOXI {'эмодзи' if kind == 'emoji' else 'стикеры'} · {user.first_name}"[:64]
    stickers = [InputSticker(sticker=InputFile(io.BytesIO(t), filename=f"s{i}.tgs"), emoji_list=[e if e else "✨"], format="animated")
                for i, (t, e) in enumerate(items)]
    try:
        await ctx.bot.get_sticker_set(name)
        exists = True
    except TelegramError:
        exists = False
    if not exists:
        await ctx.bot.create_new_sticker_set(user_id=user.id, name=name, title=title, stickers=stickers[:1], sticker_type=stype)
        stickers = stickers[1:]
    for st in stickers:
        await ctx.bot.add_sticker_to_set(user_id=user.id, name=name, sticker=st)
    return f"https://t.me/{'addemoji' if kind == 'emoji' else 'addstickers'}/{name}"


async def my_packs(u, ctx):
    me = await ctx.bot.get_me()
    lines = []
    for kind, name in pack_names(me.username, u.effective_user.id).items():
        try:
            st = await ctx.bot.get_sticker_set(name)
            link = f"https://t.me/{'addemoji' if kind == 'emoji' else 'addstickers'}/{name}"
            lines.append(f"{'💎' if kind == 'emoji' else '🎭'} <a href=\"{link}\">{'Эмодзи-пак' if kind == 'emoji' else 'Стикер-пак'}</a> · {len(st.stickers)} шт.")
        except TelegramError:
            lines.append(f"{'💎 Эмодзи-пак' if kind == 'emoji' else '🎭 Стикер-пак'}: ещё не создан")
    await show(u, ctx, ("📦 <b>Мои паки</b>\n" + ui.LINE + "\n" + "\n".join(lines) +
                        "\n\nПаки создаются сами, когда выбираешь «В эмодзи-пак / В стикер-пак» перед сборкой.",
                        [[("🚀 Создать эмодзи", "n"), ("🏠 Меню", "h")]]))


# ================================================================ файлы и текст
async def on_file(u, ctx):
    m, ud = u.message, ctx.user_data
    s = S(ctx)
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
        key = "u" + str(u.effective_user.id)                # личный шаблон пользователя (в памяти)
        ud["own_tpl"] = lot
        ud["sel"] = [key]
        ud["stage"] = "input"
        found = (D.find_candidates(lot, 1) or [None])[0]
        info = f"🔎 Текст: {ui.esc(found['why'])}" if found else "🔎 Надпись не нашёл — добавлю текст отдельным слоем"
        await m.reply_text(f"📥 Файл принят. {info}", parse_mode=HTML)
        if m.caption and s["mode"] == "text":
            return await ask_dest(u, ctx, [m.caption.strip()])
        return await show(u, ctx, ui.mode_view(s, ["Свой файл"]), new=True)

    if kind == "svg":
        try:
            await asyncio.to_thread(SVG.svg_to_group, data, 90)
        except Exception as e:
            return await m.reply_text(f"Не получилось прочитать SVG: {e}")
        ud["logo"] = {"kind": "svg", "data": data}
    else:
        try:
            await asyncio.to_thread(TR.image_to_group, data, 90, s["img_colors"], s["img_grid"], None, s["img_bg"])
        except Exception as e:
            return await m.reply_text(f"Не получилось превратить картинку в вектор: {e}")
        ud["logo"] = {"kind": "img", "data": data}
    switched = ""
    if s["mode"] != "logo":
        s["mode"] = "logo"
        switched = "🔄 Переключил режим на <b>🖼 Лого</b>.\n"
    if ud.get("sel"):
        await m.reply_text(switched + "✅ Лого принято!", parse_mode=HTML)
        return await ask_dest(u, ctx, [""])
    await m.reply_text(switched + "✅ Лого принято! Теперь выбери эмодзи.", parse_mode=HTML)
    await open_catalog(u, ctx, 0)


async def on_text(u, ctx):
    ud, text = ctx.user_data, u.message.text.strip()
    s = S(ctx)
    aw = ud.get("await")
    if aw and aw[0] != "tpladd":
        ud["await"] = None
        return await handle_await(u, ctx, aw, text)
    if aw and aw[0] == "tpladd":
        return await u.message.reply_text("Жду файл .json / .tgs 📎")
    if not ud.get("sel"):
        return await show(u, ctx, ("✨ Сначала выбери эмодзи — нажми кнопку 👇", [[("🚀 Создать эмодзи", "n")], [("🏠 Меню", "h")]]), new=True)
    if s["mode"] == "logo":
        return await show(u, ctx, ("🖼 Сейчас включён режим <b>Лого</b> — пришли картинку, или переключи на текст.",
                                   [[("🔘 Переключить на 🔤 Текст", "md:text:go")], [("🏠 Меню", "h")]]), new=True)
    lines = [x.strip() for x in text.splitlines() if x.strip()][:8]
    await ask_dest(u, ctx, lines)


async def handle_await(u, ctx, aw, text):
    ud, s, m = ctx.user_data, S(ctx), u.effective_message
    kind = aw[0]
    try:
        if kind == "spec":
            spec = C.parse_spec(text)
            if aw[1] in ("fill", "stroke") and spec["mode"] == "none":
                return await m.reply_text("У эмодзи нельзя убрать заливку/контур. Сброс: off")
            apply_spec(s, aw[1], spec)
            return await show(u, ctx, ui.colors_view(ud, s), new=True)
        if kind == "logocolor":
            spec = C.parse_spec(text)
            s["logo_color"] = None if spec["mode"] in ("reset", "none") else {"mode": "static", "colors": spec["colors"][:1]}
            return await show(u, ctx, ui.logo_view(ud, s), new=True)
        if kind == "layer":
            s["layer"] = text
            return await show(u, ctx, ui.text_view(s, fonts_list()), new=True)
        if kind == "remove":
            s["remove"] = [x for x in text.split() if x][:30]
            return await show(u, ctx, ui.remove_view(s), new=True)
        if kind == "skip":
            s["skip"] = [x for x in text.split() if x][:30]
            return await show(u, ctx, ui.remove_view(s), new=True)
        if kind == "tplname" and is_admin(u):
            store.update_template(aw[1], title=text[:40])
            return await show(u, ctx, admin_tpl_view(aw[1]), new=True)
        if kind == "tplicon" and is_admin(u):
            store.update_template(aw[1], emoji=text.strip()[:4] or "🎭")
            return await show(u, ctx, admin_tpl_view(aw[1]), new=True)
        if kind == "bntitle" and is_admin(u):
            ud["abn"] = {"title": text[:40], "parts": []}
            return await show(u, ctx, ui.admin_banner(store.index(), [], text[:40]), new=True)
        if kind == "bc" and is_admin(u):
            return await broadcast(u, ctx, text)
    except ValueError as e:
        ud["await"] = aw
        await m.reply_text(f"⚠️ {e}")


# ================================================================ админка
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
    return ui.admin_tpl(key, meta, (p.stat().st_size // 1024) if meta.get("file") and p.exists() else 0)


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
    info = f"найден: {c[0]['why']}" if c else "текст не найден — но я добавлю его отдельным слоем"
    await m.reply_text(f"✅ Шаблон <b>{ui.esc(title)}</b> добавлен · {kb} КБ{warn}\n🔎 {ui.esc(info)}\nМожно кидать следующий 📎",
                       parse_mode=HTML, reply_markup=markup([[("⚙️ Управлять", f"a:t:{key}"), ("✅ Готово", "a:main")]]))
    asyncio.get_event_loop().run_in_executor(None, lambda: preview.tile_frames(key))


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
    if act == "bn":
        ud["await"] = ("bntitle",)
        return await q.message.reply_text("🧩 Как назвать баннер?")
    if act in ("bp", "bu", "bs"):
        abn = ud.get("abn")
        if not abn:
            return await show(u, ctx, admin_main_view())
        if act == "bp" and len(abn["parts"]) < 8:
            abn["parts"].append(parts[2])
        elif act == "bu" and abn["parts"]:
            abn["parts"].pop()
        elif act == "bs":
            if len(abn["parts"]) < 2:
                return await q.answer("Нужно минимум 2 части", show_alert=True)
            key = store.new_key("b")
            store.add_banner(key, abn["title"], abn["parts"])
            ud["abn"] = None
            asyncio.get_event_loop().run_in_executor(None, lambda: preview.tile_frames(key))
            return await show(u, ctx, admin_tpl_view(key))
        return await show(u, ctx, ui.admin_banner(store.index(), abn["parts"], abn["title"]))
    key = parts[2] if len(parts) > 2 else None
    if act == "t":
        return await show(u, ctx, admin_tpl_view(key))
    if act == "ren":
        ud["await"] = ("tplname", key)
        return await q.message.reply_text("✏️ Пришли новое название")
    if act == "ico":
        ud["await"] = ("tplicon", key)
        return await q.message.reply_text("😀 Пришли один эмодзи-значок для шаблона")
    if act == "hid":
        store.update_template(key, hidden=not store.index().get(key, {}).get("hidden"))
        return await show(u, ctx, admin_tpl_view(key))
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
        k, _ = ud.get("admin_cands", (None, []))
        i = int(parts[3])
        if k != key:
            return
        lot = copy.deepcopy(store.load_template(key))
        cands = D.find_candidates(lot, 6)
        if i >= len(cands):
            return
        layer = D.bake_node(lot, cands[i])
        meta = store.index()[key]
        store.add_template(key, meta["title"], lot, {**meta.get("settings", {}), "layer": layer}, meta.get("emoji", "🎭"))
        store.update_template(key, order=meta.get("order", 999))
        return await show(u, ctx, admin_tpl_view(key))
    if act == "test":
        s = S(ctx)
        sj = copy.deepcopy(P.DEFAULTS) | {"font": s["font"]}
        pk = store.expand(key)
        for i, p_ in enumerate(pk):
            data = await asyncio.to_thread(lambda p_=p_, i=i: build_one(store.load_template(p_), part_settings(sj, p_), "TEST",
                                                                         None, (i, len(pk)) if len(pk) > 1 else None))
            await send_sticker(q.message.chat, data[0])
        return await q.message.reply_text(f"🧪 Тест готов ({len(pk)} шт.)")


# ================================================================ кнопки
ALERTY = {"a", "ly", "ct"}


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

    if q.data.split(":")[0] not in ALERTY and q.data not in ("n",) and not q.data.startswith("cat:") and q.data != "cn":
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

    # ---- навигация мастера
    if cmd == "h":
        ud["await"] = None
        ud["pending"] = None
        return await show(u, ctx, home(ctx, u))
    if cmd == "n" and len(parts) == 1:
        await ack()
        return await open_catalog(u, ctx, 0, fresh=True)
    if cmd == "cat":
        await ack()
        return await open_catalog(u, ctx, int(parts[1]))
    if cmd == "ct":
        page, i = int(parts[1]), int(parts[2])
        chunk = ud.get("chunk") or []
        if i >= len(chunk):
            return await ack("Обнови страницу", True)
        k = chunk[i]
        ud["sel"] = [x for x in ud.get("sel", []) if x != k] if k in ud.get("sel", []) else ud.get("sel", []) + [k]
        cap, rows, *_ = ui.catalog_view(store.visible(), page, ud["sel"], sel_titles(ud))
        await ack()
        return await edit_caption(u, cap, rows)
    if cmd == "cn":
        if not ud.get("sel"):
            return await ack("Выбери хотя бы одно эмодзи — нажми номер", True)
        await ack()
        return await show(u, ctx, ui.mode_view(s, sel_titles(ud)))
    if cmd == "b":
        t = parts[1]
        if t == "cat":
            return await open_catalog(u, ctx, ud.get("page", 0))
        if t == "mode":
            return await show(u, ctx, ui.mode_view(s, sel_titles(ud) or ["Свой файл"]))
        if t == "paint":
            return await show(u, ctx, ui.paint_view(ud, s))
    if cmd == "md":
        s["mode"] = parts[1]
        if len(parts) > 2:                                    # переключение из подсказки «режим лого, а пришёл текст»
            return await show(u, ctx, ui.input_view(ud, s, sel_titles(ud), bool(ud.get("logo"))))
        return await show(u, ctx, ui.mode_view(s, sel_titles(ud) or ["Свой файл"]))
    if cmd == "mn":
        return await show(u, ctx, ui.paint_view(ud, s))
    if cmd == "th":
        apply_theme(s, parts[1])
        return await show(u, ctx, ui.paint_view(ud, s))
    if cmd == "pn":
        ud["stage"] = "input"
        return await show(u, ctx, ui.input_view(ud, s, sel_titles(ud) or ["Свой файл"], bool(ud.get("logo"))))
    if cmd == "pend":
        return await ask_dest(u, ctx, [""])
    if cmd == "again":
        ud["stage"] = "input"
        return await show(u, ctx, ui.input_view(ud, s, sel_titles(ud), bool(ud.get("logo"))))
    if cmd == "repaint":
        return await show(u, ctx, ui.paint_view(ud, s))
    if cmd == "go":
        await ack("🛠 Собираю…")
        return await run_jobs(u, ctx, parts[1])
    if cmd == "packs":
        return await my_packs(u, ctx)
    if cmd == "guide":
        return await show(u, ctx, ui.guide_view())

    # ---- тонкая настройка
    if cmd == "adv":
        return await show(u, ctx, ui.adv_view(s))
    if cmd == "m":
        view = parts[1]
        ud["await"] = None
        if view == "txt": return await show(u, ctx, ui.text_view(s, fonts_list()))
        if view == "col": return await show(u, ctx, ui.colors_view(ud, s))
        if view == "logo": return await show(u, ctx, ui.logo_view(ud, s))
        if view == "rm": return await show(u, ctx, ui.remove_view(s))
        if view == "set": return await show(u, ctx, ui.settings_view(ud, s))
    if cmd == "reset":
        keep = s["mode"]
        ud["s"] = copy.deepcopy(P.DEFAULTS) | {"mode": keep}
        ud["logo"] = None
        return await show(u, ctx, ui.adv_view(S(ctx)))
    if cmd == "tree":
        return await tree_cmd(u, ctx)

    # ---- цвета по частям
    if cmd == "c":
        ud["target"] = parts[1]
        return await show(u, ctx, ui.colors_view(ud, s))
    if cmd in ("cs", "cp", "cn_", "cr", "cm"):
        t = ud.get("target", "all")
        if cmd == "cm":
            ud["fxmode"] = "cycle" if ud.get("fxmode", "grad") == "grad" else "grad"
        elif cmd == "cs":
            apply_spec(s, t, {"mode": "static", "colors": [C.parse_color(ui.SWATCH[int(parts[1])][1])]})
        elif cmd == "cp":
            apply_spec(s, t, C.parse_spec(("grad " if ud.get("fxmode", "grad") == "grad" else "") + ui.PRESETS[parts[1]][2]))
        elif cmd == "cr":
            apply_spec(s, t, None)
        elif cmd == "cn_":
            if t in ("fill", "stroke"):
                return await ack("У эмодзи заливку/контур убрать нельзя", True)
            apply_spec(s, t, {"mode": "none", "colors": []})
        return await show(u, ctx, ui.colors_view(ud, s))
    if cmd == "cu":
        ud["await"] = ("spec", ud.get("target", "all"))
        return await q.message.reply_text(
            "✍️ Пришли цвета:\n<code>#ff0000</code> — один\n<code>#ff0000 #0000ff</code> — смена\n"
            "<code>grad #f00 #ff0 #0f0</code> — перелив\n<code>off</code> — сброс", parse_mode=HTML)

    # ---- числа / ширина / шрифт
    if cmd == "n":
        step, lo, hi, _ = ui.NUMS[parts[1]]
        s[parts[1]] = max(lo, min(hi, (s.get(parts[1]) or 0) + (step if parts[2] == "+" else -step)))
        back = {"size": "txt", "stroke_w": "txt", "cycles": "col"}.get(parts[1], "logo")
        return await show(u, ctx, {"txt": lambda: ui.text_view(s, fonts_list()), "col": lambda: ui.colors_view(ud, s),
                                   "logo": lambda: ui.logo_view(ud, s)}[back]())
    if cmd == "w":
        if parts[1] == "cycle":
            s["width"] = None if s["width"] == 0 else (0 if s["width"] is None else None)
        else:
            s["width"] = max(20, min(1000, (s["width"] or 300) + (20 if parts[1] == "+" else -20)))
        return await show(u, ctx, ui.text_view(s, fonts_list()))
    if cmd == "f":
        fl = fonts_list()
        if fl:
            s["font"] = fl[(fl.index(s["font"]) + 1) % len(fl)] if s["font"] in fl else fl[0]
        return await show(u, ctx, ui.text_view(s, fl))

    # ---- слой с текстом
    if cmd == "ly":
        lot = ud.get("own_tpl") or (store.load_template(store.expand(ud["sel"][0])[0]) if ud.get("sel") else None)
        if not lot:
            return await ack("Сначала выбери эмодзи", True)
        a = parts[1]
        if a == "auto":
            s["layer"] = "auto"
            return await show(u, ctx, ui.text_view(s, fonts_list()))
        if a == "cand":
            ud["cand_src"] = copy.deepcopy(lot)
            ud["cands"] = D.find_candidates(ud["cand_src"], 6)
            return await show(u, ctx, ui.candidates_view(ud["cands"]))
        if a == "in":
            ud["await"] = ("layer",)
            return await q.message.reply_text("✍️ Пришли имя слоя/папки с текстом (см. 🌲 Структура)")
        cands = ud.get("cands") or []
        i = int(a)
        if i < len(cands):
            s["layer"] = cands[i]["name"] if cands[i]["name"] and len(O.find_nodes_list(ud["cand_src"], [cands[i]["name"]])) == 1 else "auto"
        return await show(u, ctx, ui.text_view(s, fonts_list()))

    # ---- лого
    if cmd == "lb":
        s["img_bg"] = not s["img_bg"]
        return await show(u, ctx, ui.logo_view(ud, s))
    if cmd == "lr":
        ud["logo"] = None
        return await show(u, ctx, ui.logo_view(ud, s))
    if cmd == "lc":
        ud["await"] = ("logocolor",)
        return await q.message.reply_text("🎨 Пришли цвет лого, например <code>#ffffff</code> (или off — свои цвета)", parse_mode=HTML)

    # ---- удаление
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
    await app.bot.set_my_commands([BotCommand("menu", "🏠 Главное меню"), BotCommand("new", "🚀 Создать эмодзи"),
                                   BotCommand("templates", "🎞 Каталог"), BotCommand("guide", "📖 Как это работает"),
                                   BotCommand("admin", "🛠 Админ-панель")])
    preview.warm_up()
    log.info("Bot ready. DATA=%s admins=%s templates=%d", store.DATA, ADMINS, len(store.index()))


def main_app():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, track), group=-1)
    for name, fn in {"start": start, "menu": menu, "help": guide_cmd, "guide": guide_cmd, "new": new_cmd, "templates": new_cmd,
                     "admin": admin_cmd, "tree": tree_cmd}.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.Sticker.ALL | filters.PHOTO, on_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main_app()
