# Guide du référentiel réglementaire

Une règle = un fichier YAML par version dans `referentiel/rules/`. Le moteur ne contient **aucun seuil ni
formule réglementaire** : il exécute un *calculateur* générique avec les paramètres de la règle, puis compare
le résultat aux seuils de la règle.

## Structure d'une version de règle

```yaml
id: LU-UCITS-43-EMETTEUR-5-10-40     # identifiant stable de la règle
version: 1                           # incrémenter pour toute modification
name: "Dispersion par émetteur – règle 5/10/40"
category: dispersion_emetteur        # voir les catégories ci-dessous
framework: UCITS
jurisdiction: LU
definition: >                        # texte de la règle
formula: >                           # formule lisible (affichée et exportée)
calculator: rule_5_10_40             # calculateur du catalogue
params: {...}                        # paramètres du calculateur (filtres, champ montant, clé d'agrégation…)
limits:                              # un ou plusieurs seuils
  - {key: max_weight, label: "…", operator: "<=", value: "0.10"}
applicability:                       # conditions sur les caractéristiques du fonds (fiche fonds)
  all:
    - {field: legal_framework, op: eq, value: UCITS}
exceptions: ["…"]                    # exceptions et conditions d'application (affichées)
required_data: [...]                 # données nécessaires (documentation)
sources:                             # au moins une, déclarée dans sources.yaml
  - {source: LU-LOI-2010, article: "art. 43 §1", confidence: a_confirmer}
valid_from: "2010-12-17"
valid_to: null                       # à renseigner pour fermer une version remplacée
validity_note: "…"
overrides: []                        # facultatif : identifiants des règles que celle-ci remplace (ex. règle statutaire plus stricte)
validation: {by: "Nom", on: "2026-10-01", comment: "…"}   # facultatif : sinon via l'interface
```

Les valeurs sont des **fractions** de l'actif net (`"0.10"` = 10 %), écrites entre guillemets pour une précision
décimale exacte.

## Calculateurs disponibles

| Calculateur | Valeurs produites | Usage |
|---|---|---|
| `weight_by_key` | `max_weight` | Poids maximal d'une entité : émetteur (`key_field: issuer_id`), groupe (`group_id`, avec `key_fallback`), OPC… |
| `rule_5_10_40` | `max_weight`, `sum_above_threshold` | Règle 5/10/40 (`threshold`, strictement supérieur). |
| `public_issuer_issues` | `max_issue_weight`, `min_issue_count` | Dérogation émetteur public : poids par émission et nombre d'émissions des émetteurs au-delà d'`over_threshold`. |
| `counterparty_exposure` | `max_credit_institution`, `max_other` | Contrepartie sur dérivés OTC (valeurs positives), limite selon le type de contrepartie. |
| `aggregate_filter` | `total` | Total d'un ensemble filtré / actif net (actifs non éligibles, emprunts, liquidités, exposition globale…). |
| `holding_ratio` | `max_holding` | Emprise : quantité détenue / encours émis (référentiel émetteurs). |

Paramètres communs :
- `amount_field` : `total_market_value` (coupon couru inclus, par défaut), `market_value` ou `quantity`.
- `amount_transform` : `abs`, `positive` ou `negative_abs`. `amount_transform_by_type` applique une
  transformation par type d'instrument.
- `assumptions` : hypothèses de méthode. Elles sont toujours affichées et conduisent au statut « À confirmer ».
- `warn_if_types_present` : message affiché si un type d'instrument est présent en portefeuille.

## Filtres déclaratifs

```yaml
filters:
  - {field: instrument_type, op: in, value: [equity, debt_security]}
  - field: issuer_type
    op: not_in
    value: [souverain_ue]
    on_missing: include            # not_calculable (défaut) | include | exclude
    missing_note: "Texte affiché si la donnée manque"
  - not_all:                        # exclut les lignes qui satisfont TOUTES les sous-conditions
      - {field: instrument_type, op: in, value: [debt_security]}
      - {field: issuer_type, op: in, value: [souverain_ue]}
```
Opérateurs : `in`, `not_in`, `eq`, `ne`, `gt0`, `lt0`, `is_true`, `is_false`. Les filtres sont évalués dans
l'ordre. Ceux qui portent sur des colonnes exportées sont traduits en formules Excel.

Champs utilisables :
- **positions** : `instrument_type`, `currency`, `country`, `isin`, `market_name`, `counterparty`,
  `src_category`, `account_code`…
- **enrichissement** : `issuer_id`, `issuer_type`, `group_id`, `market_eligibility`, `issuer_outstanding`.

## Vocabulaires

- **Types d'instruments** (profil de mapping) : `equity`, `debt_security`, `money_market_instrument`,
  `structured_certificate`, `fund_unit`, `deposit`, `cash_account`, `cash_account_other`, `cash_pending`,
  `margin_deposit`, `fee_accrual`, `receivable`, `derivative_listed_mtm`, `derivative_listed_commitment`,
  `derivative_otc`, `derivative_otc_commitment`, `unknown`.
- **Types d'émetteurs** : `souverain_ue`, `souverain_hors_ue`, `collectivite_ue`,
  `organisme_public_international`, `etablissement_credit_ue`, `etablissement_credit_equivalent`, `entreprise`,
  `opcvm`, `fia`, `autre`.
- **Éligibilité des marchés** : `marche_reglemente_ue`, `autre_marche_reglemente`, `non_eligible`, `inconnu`.
- **Catégories** : `dispersion_emetteur`, `dispersion_emetteur_public`, `concentration_groupe`,
  `depots_liquidites`, `derives`, `opc`, `actifs_eligibles`, `emprise`, `emprunts`, `geographique`, `liquidite`,
  `statutaire`, `autre`.

## Règles statutaires (prospectus)

Créer par exemple `referentiel/rules/statutaire/<fonds>/…yaml`, avec :
- une condition d'applicabilité sur une caractéristique du fonds, ou sur son identifiant
  (`{field: fund_id, op: eq, value: "133303"}`, disponible automatiquement) ;
- une source de type `prospectus` déclarée dans `sources.yaml` ;
- si la règle remplace une limite réglementaire, `overrides: [ID_REGLE]`.

## Procédure avant mise en production d'un ratio

1. Ajouter au jeu de test fictif (`tests/fixtures/generate_fonds_test.py`) les lignes qui exercent la règle.
2. Ajouter le résultat attendu, calculé à la main et commenté, dans `tests/fixtures/fonds_test/expected.yaml`.
3. Lancer `python -m pytest`.
4. Faire vérifier la règle et les sources par la conformité, puis la valider dans l'interface.
