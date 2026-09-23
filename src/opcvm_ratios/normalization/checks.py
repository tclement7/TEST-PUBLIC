"""Contrôles de cohérence sur un jeu de données assemblé (positions + actif net + changes)."""
from __future__ import annotations

from decimal import Decimal

from ..numbers import HUNDRED, ZERO, div, dsum, fmt_amount, fmt_number
from ..settings import Tolerances
from .model import BLOCKING, INFO, WARNING, Anomaly

MAX_DETAILED = 15
ROUNDING = Decimal("0.01")   # tolérance d'arrondi d'affichage (1 centime)


def isin_is_valid(isin: str) -> bool:
    if not isin or len(isin) != 12 or not isin[:2].isalpha() or not isin.isalnum():
        return False
    digits = "".join(str(int(c, 36)) for c in isin.upper())
    total, double = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return total % 10 == 0


def fx_per_fund_ccy(fx_records: list[dict], fund_ccy: str) -> dict[str, Decimal]:
    """Nombre d'unités de devise pour 1 unité de devise du fonds."""
    out = {}
    for r in fx_records:
        if r.get("base_currency") == fund_ccy and r.get("rate") is not None and r.get("quotity"):
            out[r["currency"]] = div(r["rate"], r["quotity"])
    return out


def run_checks(positions: list[dict], nav_rows: list[dict], fx: dict[str, Decimal], fund_ccy: str | None,
               tol: Tolerances) -> tuple[Decimal | None, list[Anomaly]]:
    anomalies: list[Anomaly] = []

    # 1. Actif net publié
    totals = {r.get("total_net_assets") for r in nav_rows if r.get("total_net_assets") is not None}
    nav = None
    if not nav_rows:
        anomalies.append(Anomaly(BLOCKING, "ACTIF_NET_ABSENT", "Aucune ligne d'actif net"))
    elif len(totals) != 1:
        anomalies.append(Anomaly(BLOCKING, "ACTIF_NET_INCOHERENT",
                                 f"Valeurs d'actif net total différentes selon les lignes : {sorted(map(str, totals))}"))
    else:
        nav = totals.pop()
        if nav <= ZERO:
            anomalies.append(Anomaly(BLOCKING, "ACTIF_NET_NON_POSITIF", f"Actif net non positif : {nav}"))
            nav = None
        shares = [r.get("share_net_assets") for r in nav_rows]
        if nav is not None and all(s is not None for s in shares):
            diff = dsum(shares) - nav
            if abs(diff) > tol.nav_reconciliation_abs:
                anomalies.append(Anomaly(WARNING, "ACTIF_NET_CLASSES",
                                         f"Σ actifs nets des classes ({fmt_amount(dsum(shares), fund_ccy or '')}) ≠ actif net total "
                                         f"({fmt_amount(nav, fund_ccy or '')}) : écart {fmt_number(diff)}"))
    nav_ccys = {r.get("fund_currency") for r in nav_rows} - {None}
    pos_ccys = {p.get("fund_currency") for p in positions} - {None}
    if len(nav_ccys | pos_ccys) > 1:
        anomalies.append(Anomaly(BLOCKING, "DEVISE_FONDS_INCOHERENTE",
                                 f"Devises du fonds incohérentes entre fichiers : {sorted(nav_ccys | pos_ccys)}"))

    # 2. Rapprochement lignes de bilan / actif net
    bs = [p for p in positions if p.get("balance_sheet")]
    missing_tmv = [p for p in bs if p.get("total_market_value") is None]
    if missing_tmv:
        anomalies.append(Anomaly(WARNING, "VALEUR_MARCHE_MANQUANTE",
                                 f"{len(missing_tmv)} ligne(s) de bilan sans valeur de marché",
                                 row_ref=", ".join(p["line_id"] for p in missing_tmv[:MAX_DETAILED])))
    if nav is not None:
        total = dsum(p["total_market_value"] for p in bs if p.get("total_market_value") is not None)
        diff = total - nav
        if abs(diff) > tol.nav_reconciliation_abs:
            anomalies.append(Anomaly(WARNING, "ECART_RAPPROCHEMENT_ACTIF_NET",
                                     f"Σ lignes de bilan = {fmt_amount(total, fund_ccy or '')} ; actif net publié = "
                                     f"{fmt_amount(nav, fund_ccy or '')} ; écart = {fmt_number(diff)}"))
        else:
            anomalies.append(Anomaly(INFO, "RAPPROCHEMENT_ACTIF_NET_OK",
                                     f"Σ lignes de bilan = actif net publié ({fmt_amount(nav, fund_ccy or '')}), "
                                     f"écart {fmt_number(diff)}"))

        # 3. Poids publiés vs recalculés
        bad = []
        for p in bs:
            w, t = p.get("weight_reported_pct"), p.get("total_market_value")
            if w is None or t is None:
                continue
            if abs(div(t, nav) * HUNDRED - w) > tol.weight_reported_abs:
                bad.append(p["line_id"])
        if bad:
            anomalies.append(Anomaly(WARNING, "POIDS_INCOHERENT",
                                     f"{len(bad)} ligne(s) dont le poids publié diffère du poids recalculé",
                                     row_ref=", ".join(bad[:MAX_DETAILED])))

    # 4. Quantité × prix vs valeur locale (titres)
    for p in positions:
        if p.get("instrument_type") not in {"equity", "debt_security", "money_market_instrument",
                                            "structured_certificate", "fund_unit"}:
            continue
        q, pr, mv = p.get("quantity"), p.get("price"), p.get("market_value_local")
        if q is None or pr is None or mv is None or mv == ZERO:
            continue
        expected = q * pr / (HUNDRED if p.get("price_expression") == "2" else Decimal(1))
        if abs(expected - mv) > max(tol.price_quantity_rel * abs(mv), ROUNDING):
            anomalies.append(Anomaly(WARNING, "PRIX_QUANTITE_INCOHERENT",
                                     f"Quantité × prix = {fmt_number(expected)} ≠ valeur locale {fmt_number(mv)}",
                                     row_ref=p["line_id"], raw_value=p.get("name")))

    # 5. Conversion de change
    missing_fx = set()
    for p in bs:
        ccy, mvl, mv = p.get("currency"), p.get("market_value_local"), p.get("market_value")
        if not ccy or mvl is None or mv is None or ccy == fund_ccy or mv == ZERO:
            continue
        rate = fx.get(ccy)
        if rate is None:
            missing_fx.add(ccy)
            continue
        converted = div(mvl, rate)
        if abs(converted - mv) > max(tol.fx_rel * abs(mv), ROUNDING):
            anomalies.append(Anomaly(WARNING, "CHANGE_INCOHERENT",
                                     f"Valeur locale / cours ({fmt_number(converted, 4)}) ≠ valeur en devise du fonds ({fmt_number(mv, 4)})",
                                     row_ref=p["line_id"], raw_value=f"{ccy} {rate}"))
    if missing_fx and fx:
        anomalies.append(Anomaly(WARNING, "COURS_CHANGE_ABSENT", f"Cours de change absent pour : {sorted(missing_fx)}"))

    # 6. ISIN
    bad_isin = [p for p in positions if p.get("isin") and not isin_is_valid(p["isin"])]
    for p in bad_isin[:MAX_DETAILED]:
        anomalies.append(Anomaly(WARNING, "ISIN_INVALIDE", f"Code ISIN invalide (clé de contrôle) : {p['isin']}",
                                 row_ref=p["line_id"], raw_value=p["isin"]))

    unknown = [p for p in positions if p.get("instrument_type") == "unknown"]
    if unknown:
        anomalies.append(Anomaly(WARNING, "LIGNES_NON_CLASSEES",
                                 f"{len(unknown)} ligne(s) non classées, exclues des ratios",
                                 row_ref=", ".join(p["line_id"] for p in unknown[:MAX_DETAILED])))
    return nav, anomalies
