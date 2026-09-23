"""Arithmétique décimale exacte et formatage (affichage uniquement)."""
from __future__ import annotations

import decimal
from decimal import Decimal, InvalidOperation

# Contexte dédié : précision élevée, arrondi bancaire. Le calcul n'arrondit jamais ;
# seul l'affichage arrondit.
CTX = decimal.Context(prec=34, rounding=decimal.ROUND_HALF_EVEN)

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


class NumberParseError(ValueError):
    pass


def parse_decimal(raw: str, decimal_sep: str = ".", thousands_sep: str = "") -> Decimal | None:
    """Convertit un texte en Decimal. Retourne None si vide. Lève NumberParseError si illisible."""
    if raw is None:
        return None
    s = str(raw).strip().replace(" ", "").replace(" ", "")
    if s == "":
        return None
    if thousands_sep:
        s = s.replace(thousands_sep, "")
    if decimal_sep != ".":
        s = s.replace(decimal_sep, ".")
    if s.startswith("+"):
        s = s[1:]
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise NumberParseError(f"Nombre illisible : {raw!r}") from exc
    if not value.is_finite():
        raise NumberParseError(f"Nombre non fini : {raw!r}")
    return value


def div(a: Decimal, b: Decimal) -> Decimal:
    return CTX.divide(a, b)


def add(*values: Decimal) -> Decimal:
    total = ZERO
    for v in values:
        total = CTX.add(total, v)
    return total


def dsum(values) -> Decimal:
    total = ZERO
    for v in values:
        total = CTX.add(total, v)
    return total


def to_str(value: Decimal | None) -> str | None:
    """Représentation canonique stockée (sans perte)."""
    if value is None:
        return None
    return format(value, "f")


def from_str(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _group_thousands(int_part: str, sep: str = " ") -> str:
    neg = int_part.startswith("-")
    digits = int_part[1:] if neg else int_part
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return ("-" if neg else "") + sep.join(groups)


def fmt_number(value: Decimal | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    q = Decimal(1).scaleb(-decimals)
    v = Decimal(value).quantize(q, rounding=decimal.ROUND_HALF_EVEN)
    s = format(v, "f")
    if "." in s:
        int_part, frac = s.split(".")
    else:
        int_part, frac = s, ""
    if int_part in ("-0",) and set(frac) <= {"0"}:
        int_part = "0"
    out = _group_thousands(int_part)
    return f"{out},{frac}" if decimals > 0 else out


def fmt_pct(fraction: Decimal | None, decimals: int = 2) -> str:
    """0.0842 -> '8,42 %'."""
    if fraction is None:
        return "—"
    return f"{fmt_number(Decimal(fraction) * HUNDRED, decimals)} %"


def fmt_points(fraction: Decimal | None, decimals: int = 2) -> str:
    """Écart exprimé en points de pourcentage."""
    if fraction is None:
        return "—"
    return f"{fmt_number(Decimal(fraction) * HUNDRED, decimals)} pt"


def fmt_amount(value: Decimal | None, currency: str = "EUR", decimals: int = 2) -> str:
    if value is None:
        return "—"
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency)
    return f"{fmt_number(value, decimals)} {symbol}"


def fmt_date(value) -> str:
    """'2026-09-21' -> '21/09/2026' ; '2026-09-23T12:46:22' -> '23/09/2026 12:46'."""
    if value in (None, ""):
        return "—"
    s = str(value)
    try:
        if "T" in s or " " in s.strip():
            import datetime as _dt
            d = _dt.datetime.fromisoformat(s)
            return d.strftime("%d/%m/%Y %H:%M")
        y, m, d = s[:10].split("-")
        return f"{d}/{m}/{y}"
    except ValueError:
        return s
