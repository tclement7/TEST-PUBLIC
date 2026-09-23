"""Référentiels émetteurs / groupes / marchés et enrichissement des positions.

Aucune information n'est déduite : un émetteur absent du référentiel est traité selon une hypothèse
explicite (1 instrument = 1 émetteur), tracée dans le résultat.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import jsonutil
from .numbers import parse_decimal

ISSUER_TYPES = {
    "souverain_ue": "État membre de l'UE",
    "souverain_hors_ue": "État tiers",
    "collectivite_ue": "Collectivité publique territoriale d'un État membre",
    "organisme_public_international": "Organisme public international",
    "etablissement_credit_ue": "Établissement de crédit (UE)",
    "etablissement_credit_equivalent": "Établissement de crédit soumis à des règles prudentielles équivalentes",
    "entreprise": "Entreprise",
    "opcvm": "OPCVM",
    "fia": "OPC autre qu'OPCVM",
    "autre": "Autre",
}
MARKET_ELIGIBILITY = {"marche_reglemente_ue", "autre_marche_reglemente", "non_eligible", "inconnu"}

ISSUER_COLUMNS = ["identifiant", "type_identifiant", "emetteur_id", "emetteur_nom", "groupe_id", "groupe_nom",
                  "type_emetteur", "encours_emis", "statut", "source", "commentaire"]


@dataclass
class IssuerEntry:
    identifier: str
    id_type: str
    issuer_id: str
    issuer_name: str
    group_id: str | None
    group_name: str | None
    issuer_type: str | None
    outstanding: Decimal | None
    status: str
    source: str
    row: int


@dataclass
class Referentials:
    issuers: dict[tuple[str, str], IssuerEntry] = field(default_factory=dict)
    markets: dict[str, dict] = field(default_factory=dict)
    issuers_sha: str | None = None
    markets_sha: str | None = None
    errors: list[str] = field(default_factory=list)

    def fingerprint(self) -> dict:
        return {"issuers_sha": self.issuers_sha, "markets_sha": self.markets_sha}


def _read_csv(path: Path) -> list[dict]:
    text = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            decoded = text.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    delim = ";" if decoded.splitlines()[0].count(";") >= decoded.splitlines()[0].count(",") else ","
    return list(csv.DictReader(decoded.splitlines(), delimiter=delim))


def load_referentials(issuers_file: Path | None, markets_file: Path | None) -> Referentials:
    ref = Referentials()
    if issuers_file and issuers_file.exists():
        ref.issuers_sha = jsonutil.sha256_file(issuers_file)
        for i, r in enumerate(_read_csv(issuers_file), start=2):
            ident = (r.get("identifiant") or "").strip()
            if not ident:
                continue
            itype = (r.get("type_emetteur") or "").strip() or None
            if itype and itype not in ISSUER_TYPES:
                ref.errors.append(f"emetteurs.csv ligne {i} : type d'émetteur inconnu {itype!r}")
                itype = None
            try:
                outstanding = parse_decimal(r.get("encours_emis") or "")
            except ValueError:
                ref.errors.append(f"emetteurs.csv ligne {i} : encours illisible {r.get('encours_emis')!r}")
                outstanding = None
            key = ((r.get("type_identifiant") or "ISIN").strip().upper(), ident)
            if key in ref.issuers:
                ref.errors.append(f"emetteurs.csv ligne {i} : identifiant en double {ident!r}")
                continue
            ref.issuers[key] = IssuerEntry(
                identifier=ident, id_type=key[0],
                issuer_id=(r.get("emetteur_id") or "").strip() or ident,
                issuer_name=(r.get("emetteur_nom") or "").strip() or ident,
                group_id=(r.get("groupe_id") or "").strip() or None,
                group_name=(r.get("groupe_nom") or "").strip() or None,
                issuer_type=itype, outstanding=outstanding,
                status=(r.get("statut") or "a_valider").strip(), source=(r.get("source") or "").strip(), row=i)
    if markets_file and markets_file.exists():
        ref.markets_sha = jsonutil.sha256_file(markets_file)
        for i, r in enumerate(_read_csv(markets_file), start=2):
            name = (r.get("marche") or "").strip()
            if not name:
                continue
            elig = (r.get("eligibilite") or "inconnu").strip()
            if elig not in MARKET_ELIGIBILITY:
                ref.errors.append(f"marches.csv ligne {i} : éligibilité inconnue {elig!r}")
                elig = "inconnu"
            ref.markets[name] = {"eligibility": elig, "status": (r.get("statut") or "a_valider").strip(),
                                 "source": (r.get("source") or "").strip(), "row": i}
    return ref


def enrich_positions(positions: list[dict], ref: Referentials) -> list[dict]:
    """Ajoute émetteur, groupe, type d'émetteur, éligibilité du marché. Chaque ajout porte sa provenance."""
    out = []
    for p in positions:
        e = dict(p)
        entry = None
        lookup = None
        if p.get("isin"):
            entry = ref.issuers.get(("ISIN", p["isin"]))
            lookup = ("ISIN", p["isin"])
        if entry is None and p.get("instrument_code"):
            entry = ref.issuers.get(("CODE_INSTRUMENT", p["instrument_code"])) or entry
        if entry is None and p.get("counterparty"):
            entry = ref.issuers.get(("CONTREPARTIE", p["counterparty"]))
            lookup = lookup or ("CONTREPARTIE", p["counterparty"])
        if entry is not None:
            e.update(issuer_id=entry.issuer_id, issuer_name=entry.issuer_name, group_id=entry.group_id,
                     group_name=entry.group_name, issuer_type=entry.issuer_type, issuer_outstanding=entry.outstanding,
                     issuer_source=f"référentiel émetteurs ligne {entry.row} ({entry.status})",
                     issuer_validated=entry.status == "valide")
        else:
            # Hypothèse explicite : l'instrument (ou la contrepartie) est son propre émetteur.
            if p.get("isin"):
                iid, name = f"ISIN:{p['isin']}", p.get("name")
            elif p.get("counterparty"):
                iid, name = f"CPTY:{p['counterparty']}", p["counterparty"]
            elif p.get("instrument_code"):
                iid, name = f"CODE:{p['instrument_code']}", p.get("name")
            else:
                iid, name = None, None
            e.update(issuer_id=iid, issuer_name=name, group_id=None, group_name=None, issuer_type=None,
                     issuer_outstanding=None,
                     issuer_source="non référencé – hypothèse : 1 instrument = 1 émetteur" if iid else None,
                     issuer_validated=False)
        mk = p.get("market_name")
        m = ref.markets.get(mk) if mk else None
        if m and m["eligibility"] != "inconnu":
            e.update(market_eligibility=m["eligibility"], market_validated=m["status"] == "valide",
                     market_source=f"référentiel marchés ligne {m['row']} ({m['status']})")
        else:
            e.update(market_eligibility=None, market_validated=False,
                     market_source="marché non renseigné" if not mk else f"marché {mk!r} non qualifié dans le référentiel")
        e["instrument_key"] = p.get("isin") or p.get("instrument_code")
        out.append(e)
    return out


def draft_issuers_file(positions: list[dict], path: Path, existing: Referentials | None = None) -> int:
    """Crée / complète un brouillon de référentiel émetteurs à partir des positions (statut a_valider).
    Le brouillon reprend uniquement le libellé de l'instrument : l'émetteur, le groupe et le type
    doivent être renseignés par l'utilisateur."""
    path.parent.mkdir(parents=True, exist_ok=True)
    known = set((existing.issuers if existing else {}).keys())
    rows = []
    seen = set()
    for p in positions:
        if p.get("instrument_type") in {"equity", "debt_security", "money_market_instrument", "structured_certificate",
                                        "fund_unit", "deposit", "derivative_otc"} and p.get("isin"):
            key = ("ISIN", p["isin"])
        elif p.get("counterparty") and p.get("instrument_type") in {"cash_account", "cash_account_other",
                                                                      "margin_deposit", "deposit"}:
            key = ("CONTREPARTIE", p["counterparty"])
        else:
            continue
        if key in known or key in seen:
            continue
        seen.add(key)
        rows.append({"identifiant": key[1], "type_identifiant": key[0], "emetteur_id": "",
                     "emetteur_nom": p.get("name") if key[0] == "ISIN" else p["counterparty"],
                     "groupe_id": "", "groupe_nom": "", "type_emetteur": "", "encours_emis": "",
                     "statut": "a_valider", "source": "Brouillon généré depuis les positions",
                     "commentaire": p.get("instrument_label", "")})
    new_file = not path.exists()
    with open(path, "a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ISSUER_COLUMNS, delimiter=";")
        if new_file:
            w.writeheader()
        w.writerows(rows)
    return len(rows)
