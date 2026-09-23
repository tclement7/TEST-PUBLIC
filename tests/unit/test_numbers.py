from decimal import Decimal

import pytest

from opcvm_ratios.normalization.checks import isin_is_valid
from opcvm_ratios.numbers import NumberParseError, fmt_amount, fmt_date, fmt_pct, parse_decimal


@pytest.mark.parametrize("raw,expected", [
    ("+000003667281.23000", Decimal("3667281.23")), (".01950325", Decimal("0.01950325")),
    ("-1775.35", Decimal("-1775.35")), ("  12 ", Decimal("12")), ("", None),
])
def test_parse_decimal_dot(raw, expected):
    assert parse_decimal(raw) == expected


def test_parse_decimal_french_format():
    assert parse_decimal("8 420 000,50", decimal_sep=",", thousands_sep=" ") == Decimal("8420000.50")


def test_parse_decimal_rejects_garbage():
    with pytest.raises(NumberParseError):
        parse_decimal("12,3,4")


def test_formatting():
    assert fmt_pct(Decimal("0.0842")) == "8,42 %"
    assert fmt_pct(Decimal("0.09999")) == "10,00 %"
    assert fmt_amount(Decimal("8420000")) == "8 420 000,00 €"
    assert fmt_amount(Decimal("-13713781.064")) == "-13 713 781,06 €"
    assert fmt_date("2026-09-21") == "21/09/2026"
    assert fmt_date("2026-09-23T12:46:22") == "23/09/2026 12:46"


def test_display_rounding_does_not_affect_internal_precision():
    v = Decimal("12345678") / Decimal("98765432.10")
    assert fmt_pct(v) == "12,50 %"
    assert str(v).startswith("0.12499998974843750")


@pytest.mark.parametrize("isin,ok", [("LU1229132797", True), ("FR0000121964", True), ("US67066G1040", True),
                                     ("LU1229132798", False), ("-1", False), ("", False)])
def test_isin_checksum(isin, ok):
    assert isin_is_valid(isin) is ok
