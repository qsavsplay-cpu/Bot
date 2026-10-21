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
from telegram.error import BadRequest, RetryAfter, TelegramError, TimedOut
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, TypeHandler, filters)
try:
    from telegram.ext import AIORateLimiter
except Exception:                                   # нет extra [rate-limiter] — работаем без него
    AIORateLimiter = None

import colors as C
import detect as D
import lottie_ops as O
import lottie_text as T
import pipeline as P
import preview
import pricing as PR
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
PAUSE_SEC = 90
PAY_RUB = os.environ.get("PAY_RUB", "")
PAY_CRYPTO = os.environ.get("PAY_CRYPTO", "")
ADMIN_CONTACT = os.environ.get("ADMIN_CONTACT", "")
RENDER_SEM = None            # создаётся при старте (нужен event loop)
_waiting = [0]
HTML = ParseMode.HTML
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("toxi")
_pages = {}        # кэш GIF-страниц каталога: ключ -> (bytes, file_id)


# ================================================================ нагрузка / flood
_guard = {"until": 0.0, "notified": [], "timeouts": []}


def paused():
    return time.time() < _guard["until"]


def retry_seconds(e):
    ra = getattr(e, "retry_after", 0)
    return float(ra.total_seconds() if hasattr(ra, "total_seconds") else ra or 0)


async def overload(bot, reason, seconds=PAUSE_SEC):
    """Нагрузка: пишем активным пользователям «Нагрузка сервера» и отключаемся на ~1.5 минуты."""
    if paused():
        return
    seconds = max(PAUSE_SEC, int(seconds))
    _guard["until"] = time.time() + seconds
    log.warning("OVERLOAD (%s): пауза %ss", reason, seconds)
    targets = store.active_users(10)[:60]
    _guard["notified"] = targets
    for uid in targets:
        try:
            await bot.send_message(uid, f"⚠️ <b>Нагрузка сервера</b>\nБот отключён на {max(1, round(seconds / 60, 1)):g} мин. Подожди и попробуй снова.", parse_mode=HTML)
        except TelegramError:
            pass
        await asyncio.sleep(0.08)

    async def resume():
        await asyncio.sleep(max(0, _guard["until"] - time.time()))
        for uid in _guard["notified"]:
            try:
                await bot.send_message(uid, "✅ Бот снова работает!")
            except TelegramError:
                pass
            await asyncio.sleep(0.08)
    asyncio.create_task(resume())


async def gate(u, ctx):
    """Пока идёт пауза — все апдейты (кроме админов) глотаем."""
    if u.effective_user and u.effective_user.id in ADMINS:
        return
    if not paused():
        try:
            if os.getloadavg()[0] > (os.cpu_count() or 1) * 6:
                await overload(ctx.bot, "loadavg")
        except (OSError, AttributeError):
            pass
        if not paused():
            return
    if u.callback_query:
        try:
            left = int(_guard["until"] - time.time())
            await u.callback_query.answer(f"Нагрузка сервера. Подожди {left} сек.", show_alert=True)
        except TelegramError:
            pass
    raise ApplicationHandlerStop


async def tg(fn, *a, **k):
    """Вызов Telegram с ожиданием при flood-лимите (короткие ждём, длинные — это перегрузка)."""
    for attempt in range(3):
        try:
            return await fn(*a, **k)
        except RetryAfter as e:
            wait = retry_seconds(e)
            if wait > 45 or attempt == 2:
                raise
            await asyncio.sleep(wait + 1)


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


def pay_info(cur):
    text = PAY_RUB if cur == "rub" else PAY_CRYPTO
    if text:
        return "💳 <b>Реквизиты:</b>\n" + ui.esc(text)
    return "💳 Реквизиты выдаст админ" + (f": {ui.esc(ADMIN_CONTACT)}" if ADMIN_CONTACT else " — напиши ему после заказа")


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


def bal_of(u):
    return None if is_admin(u) else store.balance(u.effective_user.id)


def home(ctx, u, notice=None):
    return ui.home_view(ctx.user_data, S(ctx), is_admin(u), notice, bal_of(u))


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


async def balance_cmd(u, ctx):
    await show(u, ctx, ui.balance_view(bal_of(u), u.effective_user.id), new=True)


async def shop_cmd(u, ctx):
    await show(u, ctx, ui.shop_view(), new=True)


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
    ck = tuple((k, t, store.index()[k].get("emoji"), store.index()[k].get("file", "")) for k, t in items)
    q = u.callback_query
    gif = None
    if ck in _pages:
        gif = _pages[ck]
    else:
        note = None
        if q:
            await q.answer("⏳ Рисую превью…")
        else:
            note = await u.effective_message.reply_text("⏳ <i>Рисую превью каталога…</i>", parse_mode=HTML)
        try:
            gif = await asyncio.to_thread(preview.page_gif, items, page * ui.PAGE + 1)
            _pages[ck] = gif
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
    def media():
        return InputFile(io.BytesIO(gif), filename="catalog.gif")      # новый поток на каждую попытку
    try:
        if q and q.message and q.message.animation:
            await tg(q.edit_message_media, InputMediaAnimation(media=media(), caption=cap, parse_mode=HTML), reply_markup=kb)
        else:
            if q:
                try:
                    await q.message.delete()
                except TelegramError:
                    pass
            await tg(u.effective_message.chat.send_animation, media(), caption=cap, parse_mode=HTML, reply_markup=kb)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        log.warning("catalog edit failed (%s) — отправляю заново", e)      # например «media not found»
        try:
            if q:
                await q.message.delete()
        except TelegramError:
            pass
        await tg(u.effective_message.chat.send_animation, media(), caption=cap, parse_mode=HTML, reply_markup=kb)


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
        return await tg(chat.send_sticker, InputFile(io.BytesIO(tgs), filename=name))
    except RetryAfter:
        raise
    except TelegramError:
        return await tg(chat.send_document, InputFile(io.BytesIO(tgs), filename=name))


def plan_jobs(ud, s, lines):
    """Список задач сборки: (ключ-для-значка, ключ-файла, строка, (i, N) | None)."""
    sel = ud["sel"]
    lines = lines if s["mode"] == "text" else [""]
    n = int(s.get("banner") or 0)
    jobs = []
    if n:                                                  # баннер: одна строка текста -> N частей подряд
        if len(sel) > 1:
            base = [pk for k in sel for pk in store.expand(k)]
        else:
            exp = store.expand(sel[0])
            base = exp if len(exp) > 1 else exp * max(2, n)
        base = base[:8]
        hetero = len(set(base)) > 1                           # разные эмодзи: текст кладём единым слоем поверх
        for line in lines:
            for i, pk in enumerate(base):
                part = ((i, len(base), "overlay") if hetero else (i, len(base))) if s["mode"] == "text" else None
                jobs.append((sel[0], pk, line, part))
        return jobs
    for key in sel:
        parts = store.expand(key)
        for line in lines:
            for i, pk in enumerate(parts):
                jobs.append((key, pk, line, (i, len(parts)) if len(parts) > 1 and s["mode"] == "text" else None))
    return jobs


async def ask_dest(u, ctx, lines):
    ud, s = ctx.user_data, S(ctx)
    jobs = plan_jobs(ud, s, lines)
    need = min(len(jobs), MAX_JOBS)
    bal = bal_of(u)
    if bal is not None and bal < need:
        return await show(u, ctx, ui.nounits_view(need, bal), new=True)
    ud["pending"] = {"lines": lines}
    await show(u, ctx, ui.dest_view(len(jobs), lines, s["mode"], need, bal), new=True)


async def run_jobs(u, ctx, dest, target=None):
    """dest: send | emoji | sticker; target: None (новый пак) или индекс существующего."""
    ud, s = ctx.user_data, S(ctx)
    chat, uid = u.effective_chat, u.effective_user.id
    pend = ud.pop("pending", None)
    if not pend or not ud.get("sel"):
        return await show(u, ctx, home(ctx, u, "Сначала выбери эмодзи и текст"), new=True)
    if not s["font"]:
        return await chat.send_message("Нет шрифтов в папке fonts/")
    if s["mode"] == "logo" and not ud.get("logo"):
        return await chat.send_message("Сначала пришли лого 🖼")
    jobs = plan_jobs(ud, s, pend["lines"])
    skipped = max(0, len(jobs) - MAX_JOBS)
    jobs = jobs[:MAX_JOBS]
    bal = bal_of(u)
    if bal is not None and bal < len(jobs):
        return await show(u, ctx, ui.nounits_view(len(jobs), bal), new=True)
    if paused():
        return await chat.send_message("⚠️ Нагрузка сервера. Попробуй через минуту.")
    wait = await chat.send_message(f"⏳ <b>Собираю</b> 0/{len(jobs)}…", parse_mode=HTML)
    results, errors, notes = [], 0, set()
    for n, (key, pk, line, part) in enumerate(jobs, 1):
        meta = store.index().get(pk, {})
        _waiting[0] += 1
        if _waiting[0] > 6:                                   # слишком много одновременных сборок
            _waiting[0] -= 1
            await overload(ctx.bot, "queue")
            return
        try:
            async with RENDER_SEM:
                lot = get_lot(ud, pk)
                tgs, rep, lvl = await asyncio.to_thread(build_one, lot, part_settings(s, pk), line, ud.get("logo"), part)
            if rep.get("fallback"):
                notes.add("в одном шаблоне не было места под надпись — добавил её отдельным слоем")
            if lvl:
                notes.add("🗜 часть файлов сжата под лимит Telegram")
            if len(tgs) > LIMIT:
                notes.add(f"⚠️ «{meta.get('title', pk)}»: {len(tgs) // 1024} КБ — больше лимита 64 КБ")
            results.append((tgs, store.index().get(key, {}).get("emoji", "✨")))
        except Exception:
            log.exception("job failed")
            errors += 1
        finally:
            _waiting[0] -= 1
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
    link, delivered = None, True
    if dest == "send":
        await deliver_files(chat, results)
    else:
        try:
            link = await add_to_pack(ctx, u.effective_user, dest, results, target)
        except RetryAfter as e:
            await overload(ctx.bot, "flood", retry_seconds(e) + 5)
            notes.add("Telegram просит подождать — отправляю файлами")
            await deliver_files(chat, results)
        except TelegramError as e:
            log.warning("pack failed: %s", e)
            notes.add(f"💎 Telegram не принял пак: <code>{ui.esc(str(e))}</code>")
            await deliver_files(chat, results)
            await chat.send_message(manual_steps(dest), parse_mode=HTML, disable_web_page_preview=True)
    if bal is not None:                                       # списываем штуки за собранное
        store.add_balance(uid, -len(results))
    store.count_render(uid, len(results))
    note = [f"✅ <b>Готово: {len(results)} шт.</b>"]
    if link:
        label = "эмодзи-пак" if dest == "emoji" else "стикер-пак"
        note.append(f"{'💎' if dest == 'emoji' else '🎭'} <a href=\"{link}\">Открыть {label}</a>\n<code>{link}</code>")
        if dest == "emoji":
            note.append("<i>Использовать эмодзи из пака смогут обладатели Premium.</i>")
    if bal is not None:
        note.append(f"💰 Осталось: <b>{store.balance(uid)} шт.</b>")
    if errors:
        note.append(f"❗️ не собралось: {errors} (штуки за них не списаны)")
    if skipped:
        note.append(f"✂️ лимит {MAX_JOBS} за раз — лишнее пропущено ({skipped})")
    note += sorted(notes)
    ud["stage"] = "done"
    await show(u, ctx, ui.done_view(s, "\n".join(note)), new=True)


async def deliver_files(chat, results):
    for tgs, _ in results:
        await send_sticker(chat, tgs)
        await asyncio.sleep(0.4)


def manual_steps(kind):
    cmd = "/newemojipack" if kind == "emoji" else "/newanimated"
    return (f"🛠 <b>Собрать вручную через @stickers</b>\n1. Открой @stickers\n2. Команда <code>{cmd}</code>\n"
            "3. Для эмодзи выбери <b>Animated</b>\n4. Придумай название\n5. Пришли файлы выше по очереди "
            "(пересылкой из чата подойдёт) и к каждому смайлик\n6. <code>/publish</code> → <code>/skip</code> → короткое имя ссылки")


# ================================================================ паки
def pack_prefix(kind):
    return "e" if kind == "emoji" else "s"


async def add_to_pack(ctx, user, kind, items, target=None):
    """Создаёт (или дополняет) пак эмодзи/стикеров. Имя оканчивается на _by_<username бота>."""
    me = await tg(ctx.bot.get_me)
    uname = me.username
    stype = StickerType.CUSTOM_EMOJI if kind == "emoji" else StickerType.REGULAR

    def mk(i, tgs, emo):
        return InputSticker(sticker=InputFile(io.BytesIO(tgs), filename=f"emoji{i}.tgs"), emoji_list=[emo or "✨"], format="animated")

    existing = store.packs(user.id, kind)
    if target is None:
        name = f"{pack_prefix(kind)}{user.id}x{int(time.time()) % 10 ** 9:x}_by_{uname}"
        title = f"Emoji Owner · {user.first_name} #{len(existing) + 1}"[:64]
        first, rest = items[0], items[1:]
        try:
            await tg(ctx.bot.create_new_sticker_set, user_id=user.id, name=name, title=title,
                     stickers=[mk(0, first[0], first[1])], sticker_type=stype)
        except BadRequest as e:
            if "emoji" not in str(e).lower():
                raise
            await tg(ctx.bot.create_new_sticker_set, user_id=user.id, name=name, title=title,
                     stickers=[mk(0, first[0], "✨")], sticker_type=stype)
        store.add_pack(user.id, kind, name, title)
        store.bump_pack(user.id, kind, name, 1)
    else:
        name = existing[int(target)]["name"]
        rest = items
    for i, (tgs, emo) in enumerate(rest, 1):
        try:
            await tg(ctx.bot.add_sticker_to_set, user_id=user.id, name=name, sticker=mk(i, tgs, emo))
        except BadRequest as e:
            if "emoji" in str(e).lower() and emo != "✨":
                await tg(ctx.bot.add_sticker_to_set, user_id=user.id, name=name, sticker=mk(i, tgs, "✨"))
            else:
                raise
        store.bump_pack(user.id, kind, name, 1)
        await asyncio.sleep(0.5)
    return f"https://t.me/{'addemoji' if kind == 'emoji' else 'addstickers'}/{name}"


async def my_packs(u, ctx):
    uid = u.effective_user.id
    lines = []
    for kind in ("emoji", "sticker"):
        for p in store.packs(uid, kind)[-6:]:
            link = f"https://t.me/{'addemoji' if kind == 'emoji' else 'addstickers'}/{p['name']}"
            lines.append(f"{'💎' if kind == 'emoji' else '🎭'} <a href=\"{link}\">{ui.esc(p['title'])}</a> · {p.get('count', 0)} шт.")
    await show(u, ctx, ("📦 <b>Мои паки</b>\n" + ui.LINE + "\n" + ("\n".join(lines) or "Пока пусто.") +
                        "\n\nПак создаётся, когда выбираешь «В эмодзи-пак / В стикер-пак» перед сборкой. "
                        "Ссылка оканчивается на <code>_by_" + ((await ctx.bot.get_me()).username or "bot") + "</code>.",
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
        if kind == "ubal" and is_admin(u):
            n = int(text.replace(" ", "").replace("+", ""))
            store.add_balance(aw[1], n)
            await notify_balance(ctx, aw[1], n)
            return await show(u, ctx, admin_user_view(aw[1]), new=True)
        if kind == "uf" and is_admin(u):
            uid2 = int(text.strip())
            if not store.user(uid2):
                return await m.reply_text("Такого пользователя нет — он должен хоть раз написать боту.")
            return await show(u, ctx, admin_user_view(uid2), new=True)
        if kind == "bc" and is_admin(u):
            return await broadcast(u, ctx, text)
    except ValueError as e:
        ud["await"] = aw
        await m.reply_text(f"⚠️ {e}")


# ================================================================ админка
def admin_main_view():
    us = store.users()
    now = time.time()
    stats = {"pending": len(store.orders("paid")), "users": len(us), "day": sum(1 for x in us.values() if now - x.get("last", 0) < 86400),
             "renders": sum(x.get("renders", 0) for x in us.values()), "tpls": len(store.index()),
             "path": str(store.DATA), "persistent": str(store.DATA) != str(store.BASE / "data")}
    return ui.admin_main(stats)


def admin_user_view(uid):
    return ui.admin_user(uid, store.user(uid) or {})


async def notify_balance(ctx, uid, delta):
    try:
        if delta > 0:
            await ctx.bot.send_message(uid, f"🎁 Тебе начислено <b>+{delta} шт.</b>\n💰 Баланс: <b>{store.balance(uid)} шт.</b>", parse_mode=HTML)
        elif delta < 0:
            await ctx.bot.send_message(uid, f"💰 Баланс изменён: <b>{delta} шт.</b>\nСейчас: <b>{store.balance(uid)} шт.</b>", parse_mode=HTML)
    except TelegramError:
        pass


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
    if act == "users":
        return await show(u, ctx, ui.admin_users(store.users(), int(parts[2])))
    if act == "uf":
        ud["await"] = ("uf",)
        return await q.message.reply_text("🔎 Пришли ID пользователя (число)")
    if act == "u":
        return await show(u, ctx, admin_user_view(int(parts[2])))
    if act == "bal":
        uid2, d_ = int(parts[2]), parts[3]
        if d_ == "zero":
            cur = store.balance(uid2)
            store.add_balance(uid2, -cur)
            await notify_balance(ctx, uid2, -cur)
        else:
            store.add_balance(uid2, int(d_))
            await notify_balance(ctx, uid2, int(d_))
        return await show(u, ctx, admin_user_view(uid2))
    if act == "ubal":
        ud["await"] = ("ubal", int(parts[2]))
        return await q.message.reply_text("✍️ Пришли число: <code>25</code> — выдать, <code>-10</code> — снять", parse_mode=HTML)
    if act == "orders":
        return await show(u, ctx, ui.admin_orders(store.orders("paid")))
    if act == "o":
        o = store.order(parts[2])
        return await show(u, ctx, ui.admin_order(parts[2], o)) if o else await ack("Нет такого заказа", True)
    if act in ("ok", "no"):
        o = store.order(parts[2])
        if not o or o["status"] in ("done", "rejected"):
            return await ack("Заказ уже обработан", True)
        if act == "ok":
            store.add_balance(o["uid"], o["units"])
            store.set_order(parts[2], status="done")
            try:
                await ctx.bot.send_message(o["uid"], f"✅ <b>Оплата подтверждена!</b>\n+{o['units']} шт. · баланс: <b>{store.balance(o['uid'])} шт.</b>", parse_mode=HTML)
            except TelegramError:
                pass
        else:
            store.set_order(parts[2], status="rejected")
            try:
                await ctx.bot.send_message(o["uid"], f"❌ Заказ #{parts[2]} отклонён. Если это ошибка — напиши админу.")
            except TelegramError:
                pass
        return await show(u, ctx, ui.admin_orders(store.orders("paid")))
    if act == "packtest":
        s = S(ctx)
        sj = copy.deepcopy(P.DEFAULTS) | {"font": s["font"]}
        tgs, *_ = await asyncio.to_thread(build_one, store.load_template(store.expand(next(iter(store.visible())))[0]), part_settings(sj, next(iter(store.visible()))), "TEST", None, None)
        out = []
        for kind in ("emoji", "sticker"):
            try:
                link = await add_to_pack(ctx, u.effective_user, kind, [(tgs, "✨")], None)
                out.append(f"✅ {kind}: {link}")
            except Exception as e:
                out.append(f"❌ {kind}: {type(e).__name__}: {e}")
        return await q.message.reply_text("🧪 <b>Тест паков</b>\n" + ui.esc("\n".join(out)), parse_mode=HTML)
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
ALERTY = {"a", "ly", "ct", "pv"}


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
    if cmd == "pv":
        idx = store.index()
        keys = (ud.get("sel") or [])[:6] if parts[1] == "sel" else (ud.get("chunk") or [])
        if not keys:
            return await ack("Сначала выбери эмодзи — нажми номер", True)
        await ack("🎬 Отправляю…")
        for k in keys:
            for pk in store.expand(k)[:4]:
                meta = idx.get(pk, {})
                if meta.get("file") and (store.TPL / meta["file"]).exists():
                    await send_sticker(q.message.chat, (store.TPL / meta["file"]).read_bytes(), f"{pk}.tgs")
                    await asyncio.sleep(0.2)
        return
    if cmd == "bn":
        a = parts[1]
        if a == "0":
            s["banner"] = 0
        elif a == "on":
            s["banner"] = s.get("banner") or 3
        else:
            s["banner"] = max(2, min(8, (s.get("banner") or 3) + (1 if a == "+" else -1)))
        return await show(u, ctx, ui.mode_view(s, sel_titles(ud) or ["Свой файл"]))
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
    if cmd == "pk":
        kind = parts[1]
        if not ud.get("pending"):
            return await show(u, ctx, home(ctx, u, "Сначала выбери эмодзи и текст"))
        total = min(len(plan_jobs(ud, s, ud["pending"]["lines"])), MAX_JOBS)
        return await show(u, ctx, ui.pack_choose_view(kind, store.packs(u.effective_user.id, kind), total))
    if cmd == "pend_back":
        return await ask_dest(u, ctx, (ud.get("pending") or {"lines": [""]})["lines"])
    if cmd == "go":
        await ack("🛠 Собираю…")
        w = parts[1]
        if w == "send":
            return await run_jobs(u, ctx, "send")
        if w in ("newemoji", "newsticker"):
            return await run_jobs(u, ctx, w[3:], None)
        if w in ("addemoji", "addsticker"):
            return await run_jobs(u, ctx, w[3:], int(parts[2]))
    if cmd == "bal":
        return await show(u, ctx, ui.balance_view(bal_of(u), u.effective_user.id))
    if cmd == "shop":
        return await show(u, ctx, ui.shop_view())
    if cmd == "sh":
        return await show(u, ctx, ui.method_view(parts[1]))
    if cmd == "pay":
        it = PR.item(parts[1])
        r, usd = PR.with_fee(it)
        amount = PR.fmt_rub(r) if parts[2] == "rub" else PR.fmt_usd(usd)
        oid = store.new_order(u.effective_user.id, parts[1], it["title"], it["units"], parts[2], amount)
        info = pay_info(parts[2])
        return await show(u, ctx, ui.order_view(oid, store.order(oid), info))
    if cmd == "pcancel":
        store.set_order(parts[1], status="cancelled")
        return await show(u, ctx, ui.shop_view())
    if cmd == "paid":
        o = store.order(parts[1])
        if not o or o["uid"] != u.effective_user.id:
            return await ack("Заказ не найден", True)
        if o["status"] in ("done", "rejected"):
            return await ack("Заказ уже обработан", True)
        store.set_order(parts[1], status="paid")
        for a in ADMINS:
            try:
                txt, rows = ui.admin_order(parts[1], store.order(parts[1]))
                await ctx.bot.send_message(a, "💳 <b>Новая оплата!</b>\n" + txt, parse_mode=HTML, reply_markup=markup(rows))
            except TelegramError:
                pass
        return await show(u, ctx, (f"⏳ <b>Заказ #{parts[1]}</b> отправлен админу на проверку.\nКак только оплату подтвердят — штуки придут на баланс.",
                                   [[("💰 Баланс", "bal"), ("🏠 Меню", "h")]]))
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
    err = ctx.error
    log.error("update failed", exc_info=err)
    if isinstance(err, RetryAfter):                        # Telegram просит подождать — отключаемся на паузу
        await overload(ctx.bot, "flood", retry_seconds(err) + 5)
        return
    if isinstance(err, TimedOut):
        now = time.time()
        _guard["timeouts"] = [t for t in _guard["timeouts"] if now - t < 60] + [now]
        if len(_guard["timeouts"]) >= 3:
            _guard["timeouts"] = []
            await overload(ctx.bot, "timeouts")
        return
    tb = "".join(traceback.format_exception(None, err, err.__traceback__))[-900:]
    try:
        if isinstance(u, Update) and u.effective_message:
            await u.effective_message.reply_text("😵 Что-то пошло не так. Попробуй ещё раз или /menu")
        for a in ADMINS:
            await ctx.bot.send_message(a, f"⚠️ Ошибка:\n<pre>{ui.esc(tb)}</pre>", parse_mode=HTML)
    except Exception:
        pass


async def post_init(app):
    global RENDER_SEM
    RENDER_SEM = asyncio.Semaphore(3)
    store.init()
    await app.bot.set_my_commands([BotCommand("menu", "🏠 Главное меню"), BotCommand("new", "🚀 Создать эмодзи"),
                                   BotCommand("templates", "🎞 Каталог"), BotCommand("guide", "📖 Как это работает"),
                                   BotCommand("balance", "💰 Баланс"), BotCommand("buy", "💳 Купить штуки"), BotCommand("admin", "🛠 Админ-панель")])
    preview.warm_up()
    log.info("Bot ready. DATA=%s admins=%s templates=%d", store.DATA, ADMINS, len(store.index()))


def main_app():
    builder = (Application.builder().token(TOKEN).post_init(post_init)
               .connect_timeout(20).read_timeout(60).write_timeout(120).pool_timeout(30)
               .get_updates_read_timeout(50).get_updates_write_timeout(30).get_updates_pool_timeout(30)
               .concurrent_updates(16))
    if AIORateLimiter is not None:
        builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
    app = builder.build()
    app.add_handler(TypeHandler(Update, gate), group=-2)
    app.add_handler(TypeHandler(Update, track), group=-1)
    for name, fn in {"start": start, "menu": menu, "help": guide_cmd, "guide": guide_cmd, "new": new_cmd, "templates": new_cmd,
                     "admin": admin_cmd, "tree": tree_cmd, "balance": balance_cmd, "buy": shop_cmd}.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.Sticker.ALL | filters.PHOTO, on_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main_app()
