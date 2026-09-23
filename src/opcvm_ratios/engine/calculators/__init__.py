"""Catalogue des calculateurs. Chaque calculateur est une fonction pure :
(contexte figé, paramètres de la règle) -> valeurs + détail + trace. Aucun accès fichier ni base.

Ajouter un type de calcul = ajouter une classe ici (et ses tests). Ajouter une règle utilisant un
calculateur existant = ajouter un fichier YAML dans referentiel/rules, sans modifier le code.
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from ...numbers import ZERO, div, dsum, fmt_amount, fmt_pct
from ..filters import FIELD_LABELS, apply_filters, describe_filter, line_amount
from ..model import NOT_RELEVANT, CalcContext, CalcOutput, DetailRow, TraceStep

MAX_REFS = 50

KEY_LABELS = {"issuer_id": "émetteur", "group_id": "groupe", "instrument_key": "instrument",
              "counterparty": "contrepartie"}


class Calculator:
    name = ""
    description = ""
    value_keys: tuple[str, ...] = ()

    def compute(self, ctx: CalcContext, params: dict) -> CalcOutput:  # pragma: no cover - interface
        raise NotImplementedError

    # ------------------------------------------------------------------ outils communs
    @staticmethod
    def _nav_step(ctx: CalcContext) -> TraceStep:
        src = ctx.nav_source or {}
        ref = f"{src.get('file', '?')} – colonne {src.get('column', '?')} – ligne(s) {src.get('rows', '?')}"
        return TraceStep("entree", "Actif net du fonds (base de calcul)", "", fmt_amount(ctx.nav, ctx.currency), [ref])

    @staticmethod
    def _filter_steps(fr, filters, amount_field, transform=None) -> list[TraceStep]:
        steps = [TraceStep("filtre", "Sélection des lignes",
                           " ET ".join(describe_filter(f) for f in filters) or "toutes les lignes",
                           f"{len(fr.included)} ligne(s) retenue(s), {fr.excluded} écartée(s)"
                           + (f" dont {len(fr.missing)} non évaluable(s) faute de donnée" if fr.missing else ""),
                           [l["line_id"] for l in fr.included][:MAX_REFS])]
        label = {"total_market_value": "valeur de marché coupon couru inclus", "market_value": "valeur de marché",
                 "quantity": "quantité / nominal"}.get(amount_field, amount_field)
        t = {"abs": " (valeur absolue)", "positive": " (montants positifs uniquement)",
             "negative_abs": " (soldes débiteurs, en valeur absolue)"}.get(transform or "", "")
        steps.append(TraceStep("entree", "Montant retenu par ligne", f"{label}{t}"))
        return steps

    @staticmethod
    def _hypotheses(fr) -> list[str]:
        out = []
        for text, lines in fr.notes.items():
            refs = ", ".join(lines[:10]) + ("…" if len(lines) > 10 else "")
            out.append(f"{text} ({len(lines)} ligne(s) : {refs})")
        return out

    @staticmethod
    def _no_nav(ctx) -> CalcOutput | None:
        if ctx.nav is None or ctx.nav <= ZERO:
            return CalcOutput(values={}, missing=["Actif net absent ou non positif"])
        return None

    @staticmethod
    def _group(ctx, lines, key_field, amount_field, transform, by_type, fallback=None, fallback_note=None):
        groups: OrderedDict[str, dict] = OrderedDict()
        missing, hyp = [], {}
        for l in lines:
            key = l.get(key_field)
            if key is None and fallback:
                key = l.get(fallback)
                if key is not None:
                    hyp.setdefault(fallback_note or f"{key_field} non renseigné", []).append(l["line_id"])
            if key is None:
                missing.append(f"{FIELD_LABELS.get(key_field, key_field).capitalize()} non déterminé — {l['line_id']} ({l.get('name') or ''})")
                continue
            if key_field in ("issuer_id", "group_id") and str(l.get("issuer_id", "")).startswith(("ISIN:", "CPTY:", "CODE:")):
                hyp.setdefault("Émetteur non référencé : hypothèse 1 instrument = 1 émetteur", []).append(l["line_id"])
            amt = line_amount(l, amount_field, transform, by_type)
            if amt is None:
                missing.append(f"Montant ({amount_field}) manquant — {l['line_id']}")
                continue
            if key_field == "group_id" and l.get("group_id") is not None:
                label = l.get("group_name") or key
            else:
                label = l.get("issuer_name") or l.get("name") or key
            g = groups.setdefault(key, {"label": label, "amount": ZERO, "lines": [], "amounts": [], "raw": []})
            g["amount"] += amt
            g["lines"].append(l["line_id"])
            g["amounts"].append(amt)
            g["raw"].append(l)
        return groups, missing, hyp

    @staticmethod
    def _entity_steps(ctx, key_label, key, g, weight) -> list[TraceStep]:
        terms = " + ".join(fmt_amount(a, ctx.currency) for a in g["amounts"][:12])
        if len(g["amounts"]) > 12:
            terms += f" + … ({len(g['amounts'])} lignes)"
        return [
            TraceStep("agregation", f"{key_label.capitalize()} {g['label']}", f"Σ = {terms}",
                      fmt_amount(g["amount"], ctx.currency), g["lines"][:MAX_REFS]),
            TraceStep("formule", "Poids dans l'actif net",
                      f"{fmt_amount(g['amount'], ctx.currency)} / {fmt_amount(ctx.nav, ctx.currency)} × 100",
                      fmt_pct(weight, 4)),
        ]


class WeightByKey(Calculator):
    name = "weight_by_key"
    description = "Poids maximal d'une entité (émetteur, groupe, contrepartie, OPC…) dans l'actif net"
    value_keys = ("max_weight",)

    def compute(self, ctx, params):
        if (o := self._no_nav(ctx)) is not None:
            return o
        filters = params.get("filters") or []
        amount_field = params.get("amount_field", "total_market_value")
        key_field = params.get("key_field", "issuer_id")
        key_label = KEY_LABELS.get(key_field, key_field)
        fr = apply_filters(ctx.positions, filters)
        groups, missing, hyp = self._group(ctx, fr.included, key_field, amount_field, params.get("amount_transform"),
                                           params.get("amount_transform_by_type"), params.get("key_fallback"),
                                           params.get("key_fallback_note"))
        out = CalcOutput(values={}, missing=fr.missing + missing, included_lines=[l["line_id"] for l in fr.included],
                         excluded_count=fr.excluded)
        out.hypotheses = self._hypotheses(fr) + [f"{t} ({len(v)} ligne(s))" for t, v in hyp.items()]
        out.trace = [self._nav_step(ctx)] + self._filter_steps(fr, filters, amount_field, params.get("amount_transform"))
        rows = []
        for key, g in groups.items():
            rows.append(DetailRow(key=key, label=g["label"], amount=g["amount"], weight=div(g["amount"], ctx.nav),
                                  line_ids=g["lines"]))
        rows.sort(key=lambda r: (-r.weight, r.key))
        out.details = rows
        out.trace.append(TraceStep("agregation", f"Agrégation par {key_label}", f"{len(rows)} entité(s)"))
        if rows:
            top = rows[0]
            out.trace += self._entity_steps(ctx, key_label, top.key, groups[top.key], top.weight)
            out.values["max_weight"] = top.weight
            out.trace.append(TraceStep("resultat", f"Poids maximal ({key_label} {top.label})", "", fmt_pct(top.weight, 4)))
        else:
            out.values["max_weight"] = ZERO
            out.trace.append(TraceStep("resultat", "Aucune ligne concernée", "", fmt_pct(ZERO)))
        return out


class Rule51040(WeightByKey):
    name = "rule_5_10_40"
    description = "Règle 5/10/40 : poids maximal par émetteur et somme des émetteurs dépassant le seuil"
    value_keys = ("max_weight", "sum_above_threshold")

    def compute(self, ctx, params):
        out = super().compute(ctx, params)
        if "max_weight" not in out.values:
            return out
        threshold = Decimal(str(params.get("threshold", "0.05")))
        above = [r for r in out.details if r.weight > threshold]
        for r in out.details:
            r.extra["above_threshold"] = r.weight > threshold
        total = dsum(r.weight for r in above)
        out.values["sum_above_threshold"] = total
        expr = " + ".join(f"{fmt_pct(r.weight, 4)} ({r.label})" for r in above) or "aucun émetteur"
        out.trace.append(TraceStep("formule", f"Somme des émetteurs dont le poids dépasse {fmt_pct(threshold)}",
                                   expr, fmt_pct(total, 4), [l for r in above for l in r.line_ids][:MAX_REFS]))
        return out


class PublicIssuerIssues(Calculator):
    name = "public_issuer_issues"
    description = "Dérogation émetteur public : poids par émission et nombre d'émissions"
    value_keys = ("max_issue_weight", "min_issue_count")

    def compute(self, ctx, params):
        if (o := self._no_nav(ctx)) is not None:
            return o
        filters = params.get("filters") or []
        amount_field = params.get("amount_field", "total_market_value")
        issue_field = params.get("issue_field", "isin")
        over = Decimal(str(params.get("over_threshold", "0.35")))
        fr = apply_filters(ctx.positions, filters)
        groups, missing, hyp = self._group(ctx, fr.included, params.get("key_field", "issuer_id"), amount_field,
                                           None, None)
        out = CalcOutput(values={}, missing=fr.missing + missing, included_lines=[l["line_id"] for l in fr.included],
                         excluded_count=fr.excluded)
        out.hypotheses = self._hypotheses(fr) + [f"{t} ({len(v)} ligne(s))" for t, v in hyp.items()]
        out.trace = [self._nav_step(ctx)] + self._filter_steps(fr, filters, amount_field)
        max_issue, counts = ZERO, []
        for key, g in groups.items():
            issues: OrderedDict[str, dict] = OrderedDict()
            for l, a in zip(g["raw"], g["amounts"]):
                ik = l.get(issue_field) or l["line_id"]
                it = issues.setdefault(ik, {"amount": ZERO, "lines": [], "label": l.get("name") or ik})
                it["amount"] += a
                it["lines"].append(l["line_id"])
            total_w = div(g["amount"], ctx.nav)
            for ik, it in issues.items():
                w = div(it["amount"], ctx.nav)
                max_issue = max(max_issue, w)
                out.details.append(DetailRow(key=ik, label=it["label"], amount=it["amount"], weight=w,
                                             line_ids=it["lines"], extra={"issuer": g["label"], "issuer_weight": total_w}))
            if total_w > over:
                counts.append(len(issues))
            out.trace.append(TraceStep("agregation", f"Émetteur public {g['label']}",
                                       f"{len(issues)} émission(s), poids total {fmt_pct(total_w, 4)}",
                                       fmt_amount(g["amount"], ctx.currency), g["lines"][:MAX_REFS]))
        out.details.sort(key=lambda r: (-r.weight, r.key))
        out.values["max_issue_weight"] = max_issue
        out.values["min_issue_count"] = Decimal(min(counts)) if counts else NOT_RELEVANT
        out.trace.append(TraceStep("resultat", "Poids maximal d'une émission", "", fmt_pct(max_issue, 4)))
        out.trace.append(TraceStep("resultat", f"Nombre minimal d'émissions (émetteurs > {fmt_pct(over)})", "",
                                   str(min(counts)) if counts else "sans objet (aucun émetteur au-delà du seuil)"))
        return out


class CounterpartyExposure(Calculator):
    name = "counterparty_exposure"
    description = "Risque de contrepartie OTC par contrepartie, limite selon le type de contrepartie"
    value_keys = ("max_credit_institution", "max_other")

    def compute(self, ctx, params):
        if (o := self._no_nav(ctx)) is not None:
            return o
        filters = params.get("filters") or []
        amount_field = params.get("amount_field", "market_value")
        ci_types = set(params.get("credit_institution_types") or [])
        fr = apply_filters(ctx.positions, filters)
        groups, missing, hyp = self._group(ctx, fr.included, params.get("key_field", "issuer_id"), amount_field,
                                           "positive", None)
        out = CalcOutput(values={}, missing=fr.missing + missing, included_lines=[l["line_id"] for l in fr.included],
                         excluded_count=fr.excluded)
        out.hypotheses = self._hypotheses(fr) + [f"{t} ({len(v)} ligne(s))" for t, v in hyp.items()]
        out.trace = [self._nav_step(ctx)] + self._filter_steps(fr, filters, amount_field, "positive")
        max_ci, max_other = ZERO, ZERO
        for key, g in groups.items():
            ctype = g["raw"][0].get("issuer_type")
            if ctype is None:
                out.missing.append(f"Type de contrepartie non renseigné — {g['label']}")
                continue
            w = div(g["amount"], ctx.nav)
            is_ci = ctype in ci_types
            if is_ci:
                max_ci = max(max_ci, w)
            else:
                max_other = max(max_other, w)
            out.details.append(DetailRow(key=key, label=g["label"], amount=g["amount"], weight=w, line_ids=g["lines"],
                                         extra={"counterparty_type": ctype, "credit_institution": is_ci}))
            out.trace += self._entity_steps(ctx, "contrepartie", key, g, w)
        out.details.sort(key=lambda r: (-r.weight, r.key))
        out.values.update(max_credit_institution=max_ci, max_other=max_other)
        if not groups:
            out.trace.append(TraceStep("resultat", "Aucun dérivé de gré à gré en portefeuille", "", fmt_pct(ZERO)))
        return out


class AggregateFilter(Calculator):
    name = "aggregate_filter"
    description = "Total d'un ensemble de lignes filtrées rapporté à l'actif net"
    value_keys = ("total",)

    def compute(self, ctx, params):
        if (o := self._no_nav(ctx)) is not None:
            return o
        filters = params.get("filters") or []
        amount_field = params.get("amount_field", "total_market_value")
        transform = params.get("amount_transform")
        fr = apply_filters(ctx.positions, filters)
        out = CalcOutput(values={}, missing=list(fr.missing), included_lines=[l["line_id"] for l in fr.included],
                         excluded_count=fr.excluded)
        out.hypotheses = self._hypotheses(fr) + list(params.get("assumptions") or [])
        present = {p.get("instrument_type") for p in ctx.positions}
        for t, msg in (params.get("warn_if_types_present") or {}).items():
            if t in present:
                out.hypotheses.append(msg)
        out.trace = [self._nav_step(ctx)] + self._filter_steps(fr, filters, amount_field, transform)
        amounts = []
        for l in fr.included:
            a = line_amount(l, amount_field, transform, params.get("amount_transform_by_type"))
            if a is None:
                out.missing.append(f"Montant ({amount_field}) manquant — {l['line_id']}")
                continue
            if a == ZERO and transform in ("positive", "negative_abs"):
                continue
            amounts.append(a)
            out.details.append(DetailRow(key=l["line_id"], label=l.get("name") or l["line_id"], amount=a,
                                         weight=div(a, ctx.nav), line_ids=[l["line_id"]],
                                         extra={"instrument_type": l.get("instrument_type"), "currency": l.get("currency"),
                                                "market": l.get("market_name")}))
        total = dsum(amounts)
        out.details.sort(key=lambda r: (-r.weight, r.key))
        w = div(total, ctx.nav)
        out.values["total"] = w
        terms = " + ".join(fmt_amount(a, ctx.currency) for a in amounts[:12]) + (" + …" if len(amounts) > 12 else "")
        out.trace.append(TraceStep("agregation", "Somme des montants retenus", f"Σ = {terms or '0'}",
                                   fmt_amount(total, ctx.currency), [d.key for d in out.details][:MAX_REFS]))
        out.trace.append(TraceStep("formule", "Rapport à l'actif net",
                                   f"{fmt_amount(total, ctx.currency)} / {fmt_amount(ctx.nav, ctx.currency)} × 100",
                                   fmt_pct(w, 4)))
        return out


class HoldingRatio(Calculator):
    name = "holding_ratio"
    description = "Emprise : quantité détenue rapportée à l'encours émis par l'émetteur"
    value_keys = ("max_holding",)

    def compute(self, ctx, params):
        filters = params.get("filters") or []
        qf = params.get("quantity_field", "quantity")
        of = params.get("outstanding_field", "issuer_outstanding")
        fr = apply_filters(ctx.positions, filters)
        groups, missing, hyp = self._group(ctx, fr.included, params.get("key_field", "issuer_id"), qf, None, None)
        out = CalcOutput(values={}, missing=fr.missing + missing, included_lines=[l["line_id"] for l in fr.included],
                         excluded_count=fr.excluded)
        out.hypotheses = self._hypotheses(fr) + [f"{t} ({len(v)} ligne(s))" for t, v in hyp.items()]
        out.trace = self._filter_steps(fr, filters, qf)
        best = ZERO
        for key, g in groups.items():
            outstanding = g["raw"][0].get(of)
            if outstanding is None or outstanding <= ZERO:
                out.missing.append(f"Encours émis non renseigné — émetteur {g['label']}")
                continue
            h = div(g["amount"], outstanding)
            best = max(best, h)
            out.details.append(DetailRow(key=key, label=g["label"], amount=g["amount"], weight=h, line_ids=g["lines"],
                                         extra={"outstanding": outstanding}))
            out.trace.append(TraceStep("formule", f"Emprise sur {g['label']}",
                                       f"{g['amount']} / {outstanding}", fmt_pct(h, 4), g["lines"][:MAX_REFS]))
        out.details.sort(key=lambda r: (-r.weight, r.key))
        out.values["max_holding"] = best
        return out


CALCULATORS: dict[str, Calculator] = {c.name: c for c in
                                      (WeightByKey(), Rule51040(), PublicIssuerIssues(), CounterpartyExposure(),
                                       AggregateFilter(), HoldingRatio())}
