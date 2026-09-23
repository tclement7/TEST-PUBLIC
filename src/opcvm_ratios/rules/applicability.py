"""Évaluation des conditions d'applicabilité sur les caractéristiques du fonds.

Logique à trois états : True (applicable), False (non applicable), None (indéterminé :
une caractéristique nécessaire n'est pas renseignée). Un résultat indéterminé n'est jamais
assimilé à "applicable" ou "non applicable" : il est signalé.
"""
from __future__ import annotations

OPS = {"eq", "ne", "in", "not_in", "is_true", "is_false", "exists"}

OP_LABELS = {"eq": "=", "ne": "≠", "in": "∈", "not_in": "∉", "is_true": "est vrai", "is_false": "est faux",
             "exists": "est renseigné"}


def _cond(attrs: dict, c: dict) -> tuple[bool | None, str]:
    f, op, v = c["field"], c.get("op", "eq"), c.get("value")
    if op not in OPS:
        raise ValueError(f"Opérateur d'applicabilité inconnu : {op}")
    val = attrs.get(f)
    label = f"{f} {OP_LABELS[op]}" + (f" {v}" if op in {'eq', 'ne', 'in', 'not_in'} else "")
    if op == "exists":
        return val is not None, f"{label} → {'oui' if val is not None else 'non'}"
    if val is None:
        return None, f"{label} → indéterminé ({f} non renseigné dans la fiche fonds)"
    if op == "eq":
        res = val == v
    elif op == "ne":
        res = val != v
    elif op == "in":
        res = val in v
    elif op == "not_in":
        res = val not in v
    elif op == "is_true":
        res = val is True
    else:
        res = val is False
    return res, f"{label} → {'oui' if res else 'non'} (valeur : {val})"


def evaluate(expr: dict, attrs: dict) -> tuple[bool | None, list[str]]:
    if not expr:
        return True, ["Aucune condition : applicable"]
    if "all" in expr:
        results, notes = [], []
        for sub in expr["all"]:
            r, n = evaluate(sub, attrs) if ("all" in sub or "any" in sub or "not" in sub) else _one(sub, attrs)
            results.append(r); notes.extend(n)
        if any(r is False for r in results):
            return False, notes
        return (None if any(r is None for r in results) else True), notes
    if "any" in expr:
        results, notes = [], []
        for sub in expr["any"]:
            r, n = evaluate(sub, attrs) if ("all" in sub or "any" in sub or "not" in sub) else _one(sub, attrs)
            results.append(r); notes.extend(n)
        if any(r is True for r in results):
            return True, notes
        return (None if any(r is None for r in results) else False), notes
    if "not" in expr:
        r, n = evaluate(expr["not"], attrs)
        return (None if r is None else not r), ["NON(" + "; ".join(n) + ")"]
    return _one(expr, attrs)


def _one(c: dict, attrs: dict):
    r, note = _cond(attrs, c)
    return r, [note]
