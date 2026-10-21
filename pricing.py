"""Цены на эмодзи. База без комиссии; комиссия: рубли +4%, крипта +1.2%."""
RUB_FEE, USD_FEE = 0.04, 0.012
# (штук, $, ₽) — без комиссии
PIECES = [(1, 0.1, 10), (5, 0.5, 50), (10, 1.0, 100), (15, 1.2, 120)]
RANGE = ((20, 1.5, 150), (100, 6.5, 500))              # 20–100 шт.: от 1.5$/150₽ до 6.5$/500₽ (линейно)
RANGE_BUTTONS = [20, 30, 50, 75, 100]
# Паки: id -> (эмодзи, подарок, $, ₽)
PACKS = {"p15": (15, 5, 1.0, 100), "p25": (25, 7, 1.5, 145), "p50": (50, 15, 2.6, 245)}


def base_price(qty):
    """(usd, rub) без комиссии для qty штук."""
    for q, usd, rub in PIECES:
        if q == qty:
            return usd, rub
    (q0, u0, r0), (q1, u1, r1) = RANGE
    if q0 <= qty <= q1:
        if qty in (q0, q1):
            return (u0, r0) if qty == q0 else (u1, r1)
        k = (qty - q0) / (q1 - q0)
        return round((u0 + (u1 - u0) * k) * 10) / 10, round((r0 + (r1 - r0) * k) / 5) * 5
    raise ValueError("Нет цены для такого количества")


def item(item_id):
    """item_id: 'n<qty>' (штуки) или 'p15/p25/p50' (пак). -> dict(title, units, usd, rub) без комиссии."""
    if item_id in PACKS:
        e, gift, usd, rub = PACKS[item_id]
        return {"id": item_id, "title": f"Пак {e}+{gift} в подарок", "units": e + gift, "usd": usd, "rub": rub}
    qty = int(item_id[1:])
    usd, rub = base_price(qty)
    return {"id": item_id, "title": f"{qty} шт.", "units": qty, "usd": usd, "rub": rub}


def with_fee(it):
    """Цены с комиссией: рубли ×1.04, крипта ×1.012."""
    return round(it["rub"] * (1 + RUB_FEE), 2), round(it["usd"] * (1 + USD_FEE), 4)


def fmt_rub(x):
    return f"{x:.2f}₽"


def fmt_usd(x):
    return f"{x:.4f}".rstrip("0").rstrip(".") + "$"


def all_items():
    ids = [f"n{q}" for q, _, _ in PIECES] + [f"n{q}" for q in RANGE_BUTTONS]
    return ids, list(PACKS)
