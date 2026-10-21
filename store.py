"""Хранилище: шаблоны, пользователи. DATA_DIR (на Railway — Volume) переживает перезапуски."""
import json
import os
import shutil
import time
from pathlib import Path

import lottie_text as T

START_BONUS = int(os.environ.get("START_BONUS", "3"))
BASE = Path(__file__).parent
BUILTIN = BASE / "builtin"


def _data_dir():
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir() and os.access("/data", os.W_OK):
        return Path("/data")
    return BASE / "data"


DATA = _data_dir()
TPL = DATA / "templates"
INDEX = DATA / "index.json"
USERS = DATA / "users.json"
ORDERS = DATA / "orders.json"
_cache = {}
_users = {}
_orders = {}


def _read(p, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return default


def _write(p, obj):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(p)


def init():
    """Создаёт папки и докладывает встроенные шаблоны (не затирая правки админа)."""
    TPL.mkdir(parents=True, exist_ok=True)
    idx = _read(INDEX, {})
    b_idx = _read(BUILTIN / "index.json", {})
    changed = False
    for key, meta in b_idx.items():
        if key not in idx and not meta.get("_deleted"):
            idx[key] = meta
            changed = True
        if meta.get("file"):
            src, dst = BUILTIN / meta["file"], TPL / meta["file"]
            if src.exists() and not dst.exists():
                shutil.copy(src, dst)
    if changed or not INDEX.exists():
        _write(INDEX, idx)
    _users.update(_read(USERS, {}))
    _orders.update(_read(ORDERS, {}))


def index(include_hidden=True):
    items = _read(INDEX, {})
    items = {k: v for k, v in items.items() if include_hidden or not v.get("hidden")}
    return dict(sorted(items.items(), key=lambda kv: (kv[1].get("order", 999), kv[1].get("title", kv[0]))))


def visible():
    return index(include_hidden=False)


def save_index(idx):
    _write(INDEX, idx)


def expand(key):
    """Ключи файлов, из которых состоит эмодзи: один или несколько (баннер)."""
    meta = index().get(key) or {}
    return [k for k in meta["parts"] if k in index()] if meta.get("parts") else [key]


def add_banner(key, title, parts, emoji="🧩", settings=None):
    idx = _read(INDEX, {})
    idx[key] = {"title": title, "emoji": emoji, "parts": parts, "settings": settings or {},
                "order": max([m.get("order", 0) for m in idx.values()] + [0]) + 1}
    _write(INDEX, idx)


def load_template(key):
    meta = index().get(key)
    if not meta or not meta.get("file"):
        return None
    p = TPL / meta["file"]
    if key not in _cache or _cache[key][0] != p.stat().st_mtime:
        if len(_cache) > 6:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (p.stat().st_mtime, T.load(p.read_bytes()))
    return _cache[key][1]


def add_template(key, title, data, settings=None, emoji="✨"):
    TPL.mkdir(parents=True, exist_ok=True)
    fn = f"{key}.tgs"
    (TPL / fn).write_bytes(T.to_tgs(data))
    idx = _read(INDEX, {})
    idx[key] = {"title": title, "file": fn, "emoji": emoji, "settings": settings or {},
                "order": max([m.get("order", 0) for m in idx.values()] + [0]) + 1}
    _write(INDEX, idx)
    _cache.pop(key, None)


def update_template(key, **fields):
    idx = _read(INDEX, {})
    if key in idx:
        for k, v in fields.items():
            if k == "settings":
                idx[key].setdefault("settings", {}).update(v)
            else:
                idx[key][k] = v
        _write(INDEX, idx)


def delete_template(key):
    idx = _read(INDEX, {})
    meta = idx.pop(key, None)
    if meta:
        try:
            if meta.get("file"):
                (TPL / meta["file"]).unlink()
        except FileNotFoundError:
            pass
        for m in idx.values():                       # баннеры, в которых была эта часть, теряют её
            if m.get("parts") and key in m["parts"]:
                m["parts"] = [p for p in m["parts"] if p != key]
        _write(INDEX, idx)
        _cache.pop(key, None)
    return bool(meta)


def new_key(prefix="t"):
    idx = _read(INDEX, {})
    n = 1
    while f"{prefix}{n}" in idx:
        n += 1
    return f"{prefix}{n}"


# ---- пользователи / баланс ----
def touch_user(uid, name):
    u = _users.get(str(uid))
    new = u is None
    if new:
        u = _users[str(uid)] = {"name": name, "first": int(time.time()), "renders": 0, "balance": START_BONUS}
    u["last"] = int(time.time())
    u["name"] = name
    if new:
        _write(USERS, _users)
    return new


def count_render(uid, n=1):
    u = _users.get(str(uid))
    if u:
        u["renders"] = u.get("renders", 0) + n
        if u["renders"] % 5 == 0:
            _write(USERS, _users)


def users():
    return _users


def user(uid):
    return _users.get(str(uid))


def balance(uid):
    return int((_users.get(str(uid)) or {}).get("balance", 0))


def add_balance(uid, delta):
    """Меняет баланс (не ниже 0), сохраняет, возвращает новый баланс или None, если юзера нет."""
    u = _users.get(str(uid))
    if u is None:
        return None
    u["balance"] = max(0, int(u.get("balance", 0)) + int(delta))
    _write(USERS, _users)
    return u["balance"]


def active_users(minutes=10):
    now = time.time()
    return [int(k) for k, v in _users.items() if now - v.get("last", 0) < minutes * 60]


def add_pack(uid, kind, name, title):
    u = _users.get(str(uid))
    if u is not None:
        u.setdefault("packs", {}).setdefault(kind, []).append({"name": name, "title": title, "count": 0})
        _write(USERS, _users)


def packs(uid, kind):
    return (_users.get(str(uid)) or {}).get("packs", {}).get(kind, [])


def bump_pack(uid, kind, name, n):
    for p in packs(uid, kind):
        if p["name"] == name:
            p["count"] = p.get("count", 0) + n
    _write(USERS, _users)


def flush():
    _write(USERS, _users)


# ---- заказы ----
def new_order(uid, item_id, title, units, currency, amount):
    oid = str(max([int(k) for k in _orders] + [0]) + 1)
    _orders[oid] = {"uid": uid, "item": item_id, "title": title, "units": units, "currency": currency,
                    "amount": amount, "status": "new", "ts": int(time.time())}
    _write(ORDERS, _orders)
    return oid


def order(oid):
    return _orders.get(str(oid))


def set_order(oid, **fields):
    if str(oid) in _orders:
        _orders[str(oid)].update(fields)
        _write(ORDERS, _orders)


def orders(status=None):
    return {k: v for k, v in sorted(_orders.items(), key=lambda kv: -int(kv[0])) if status is None or v["status"] == status}
