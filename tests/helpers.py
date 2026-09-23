"""Outils de construction de positions et de contextes pour les tests unitaires."""
import datetime as dt
from decimal import Decimal

from opcvm_ratios.engine.model import CalcContext


def pos(line_id, amount, **kw):
    d = {"line_id": line_id, "name": kw.pop("name", line_id), "total_market_value": Decimal(str(amount)),
         "market_value": Decimal(str(kw.pop("market_value", amount))), "instrument_type": kw.pop("instrument_type", "equity"),
         "issuer_validated": kw.pop("issuer_validated", True), "market_validated": kw.pop("market_validated", True)}
    d.update(kw)
    return d


def ctx(positions, nav="100000000"):
    return CalcContext(fund_id="F", fund_name="Fonds", data_date=dt.date(2026, 6, 30), currency="EUR",
                       nav=Decimal(nav) if nav is not None else None, nav_source={"file": "nav.csv", "column": "AN", "rows": [2]},
                       positions=positions)
