# Contrôle des ratios réglementaires OPCVM

Application **locale** d'aide au contrôle des ratios réglementaires et statutaires des OPCVM (UCITS).
Elle lit les fichiers déposés par l'administrateur de fonds, identifie les fonds, détermine les ratios
applicables, les calcule de façon **déterministe et traçable**, puis produit un tableau de bord, un rapport PDF
et un classeur Excel dont les calculs sont de **vraies formules**.

> ⚠ **Outil d'aide au contrôle.** Les résultats ne constituent pas une validation réglementaire définitive
> et doivent être revus par un contrôleur. Les règles livrées sont au statut **brouillon** : tant qu'elles ne sont
> pas validées par la conformité, l'application n'affiche jamais « Conforme » mais « À confirmer », avec un statut
> indicatif.

---

## 1. Installation (Windows)

Prérequis : Python 3.11 ou supérieur (<https://www.python.org>, cocher « Add to PATH »).

1. Copier le dossier du projet sur le serveur ou le poste (par exemple `D:\Outils\opcvm-ratios`).
2. Double-cliquer sur **`installer.bat`**. Le script crée un environnement isolé `.venv` et installe les dépendances.
   Sur un poste sans accès à Internet : préparer au préalable un dossier `wheelhouse` avec
   `pip download -d wheelhouse -r requirements.txt`, puis `pip install --no-index --find-links=wheelhouse -e .`.
3. Renseigner le dossier des fichiers dans **`config/settings.yaml`** (`source_dir`), par exemple
   `source_dir: '\\serveur\partage\OPCVM\extractions'`.
4. Double-cliquer sur **`lancer.bat`**. L'interface s'ouvre sur <http://127.0.0.1:8765>.

Les données de travail (base SQLite, archives des fichiers, exports, journaux) sont écrites dans `data/`.
Ce dossier est exclu du dépôt git.

## 2. Utilisation

### Interface web
| Écran | Contenu |
|---|---|
| **Tableau de bord** | Vue globale par fonds : date, nombre de ratios, conformes, proches, dépassements, à confirmer (dont dépassements indicatifs), non calculables. Boutons « Scanner les fichiers » et « Lancer le contrôle ». |
| **Fonds** | Liste des fonds : identifiant, classes, date des données, fichiers sources utilisés, date de dernière mise à jour. |
| **Vue fonds** | Ratios classés par catégorie : valeur, limite, marge, jauge, statut, source. Caractéristiques du fonds utilisées et contrôles de cohérence des données. |
| **Détail d'un ratio** | Données sources → formule → calcul pas à pas → résultat → limite → statut. Montants par entité, lignes sources (fichier et numéro de ligne), hypothèses, empreintes et historique. |
| **Historique** | Liste des calculs et évolution d'un ratio dans le temps. |
| **Fichiers & anomalies** | Registre des fichiers (empreinte, statut, colonnes reconnues ou manquantes, anomalies) et jeux de données. |
| **Référentiel** | Règles, versions, seuils, sources ; validation d'une version par la conformité. |
| **Exports** | Rapport PDF ou classeur Excel pour un fonds, plusieurs fonds ou tous les fonds. |
| **Journal** | Journal d'audit chaîné, avec vérification d'intégrité. |

### Ligne de commande
```bat
.venv\Scripts\activate
python -m opcvm_ratios scan                       & REM détecter et importer les nouveaux fichiers
python -m opcvm_ratios run                        & REM importer puis calculer
python -m opcvm_ratios run --fund 133303          & REM un seul fonds
python -m opcvm_ratios export --format both       & REM PDF + Excel du dernier calcul
python -m opcvm_ratios rules                      & REM lister le référentiel et ses erreurs
python -m opcvm_ratios draft-issuers              & REM brouillon de référentiel émetteurs
python -m opcvm_ratios verify-audit               & REM contrôle d'intégrité du journal
```
`controle_quotidien.bat` enchaîne import, calcul et exports. Il peut être programmé dans le Planificateur de
tâches Windows.

## 3. Statuts

| Statut | Signification |
|---|---|
| **Conforme** | Règle validée, données complètes, limite respectée, hors zone d'alerte. |
| **Proche de la limite** | Au-delà du **seuil d'alerte interne** défini dans la fiche fonds. Sans seuil paramétré, ce statut n'existe pas. |
| **Dépassement** | Limite réglementaire ou statutaire non respectée. |
| **À confirmer** | Calcul réalisé, mais la règle est en brouillon, une caractéristique du fonds n'est pas renseignée, ou le résultat repose sur une hypothèse ou une donnée de référentiel non validée. Le **statut indicatif** reste affiché. |
| **Non calculable** | Règle applicable mais donnée manquante ou incohérente : les champs et lignes en cause sont listés. |
| **Non applicable** | Conditions d'applicabilité non remplies, avec le motif. |
| **Conflit de règles** | Plusieurs versions ou règles concurrentes sans priorité définie. |

La **marge** est exprimée en points de pourcentage (limite − valeur) et en devise du fonds (marge × actif net).
La précision interne est complète (décimales exactes). L'arrondi n'intervient qu'à l'affichage.

## 4. Paramétrage

### Fichiers sources : `config/mappings/*.yaml`
Un **profil de mapping** décrit, pour chaque type de fichier :
- le motif du nom (alias du fonds et date) ;
- les colonnes sources de chaque champ canonique, avec leurs synonymes et les valeurs « vides » (par exemple `-1`) ;
- le typage (texte, décimal, date) ;
- la classification des lignes en types d'instruments canoniques ;
- les types de fichiers nécessaires à un jeu de données complet.

Le profil `efa_standard.yaml` couvre les extractions `ffpos1` (positions), `ffnav1` (actif net), `fflia1`
(passif), `ffcur1` (changes) et `ffmov1` (mouvements). Une nouvelle structure de fichier se traite par un
nouveau profil, sans code.

### Fiches fonds : `config/funds/*.yaml`
Identifiants et alias du fonds, classes d'actions, puis **caractéristiques versionnées** : cadre réglementaire,
domicile, type de fonds, méthode de calcul du risque global, dérogations. On y trouve aussi les seuils d'alerte
internes et les règles exclues, avec leur motif. Une caractéristique à `null` rend « indéterminée »
l'applicabilité des règles qui en dépendent.

### Référentiels
- `config/referentiels/local/emetteurs.csv` : émetteur, groupe, type d'émetteur et encours émis par ISIN ou par
  contrepartie. Ce fichier est **local et non versionné**, car il reflète les lignes détenues. Il se génère en
  brouillon avec `draft-issuers`, puis se complète et se valide (colonne `statut` = `valide`). Voir le modèle
  `config/referentiels/emetteurs_modele.csv`.
- `config/referentiels/marches.csv` : éligibilité des marchés de cotation.

Sans référentiel émetteurs, l'application applique l'hypothèse explicite « 1 instrument = 1 émetteur ». Elle la
signale dans chaque résultat concerné, qui passe alors en « À confirmer ».

## 5. Référentiel réglementaire

Les règles sont dans `referentiel/rules/**.yaml`, à raison d'un fichier par version, **indépendamment du code**.
Les sources sont déclarées dans `referentiel/sources.yaml`. Chaque règle comporte : identifiant, version, nom,
catégorie, définition, formule, calculateur, paramètres (filtres déclaratifs), seuils, conditions d'applicabilité,
exceptions, données requises, sources (avec niveau de confiance), dates de validité. Le guide détaillé est dans
[`docs/REFERENTIEL.md`](docs/REFERENTIEL.md).

- **Ajouter un ratio** reposant sur un calculateur existant : ajouter un fichier YAML, sans modifier le code.
- **Modifier une règle** : créer `…v2.yaml` avec `valid_from`, puis fermer la v1 (`valid_to`). Toute autre
  modification d'une version déjà utilisée est **rejetée**, grâce à l'empreinte SHA-256 du contenu normatif.
  Les résultats passés restent liés à la version et à l'empreinte utilisées.
- **Valider une règle** : écran Référentiel → règle → « Valider cette version ». La validation (nom, date,
  commentaire) porte sur l'empreinte exacte de la version et est journalisée.

Le référentiel livré contient 14 règles UCITS de droit luxembourgeois (loi du 17 décembre 2010, Partie I), toutes
**en brouillon, avec des références à confirmer** : dispersion 5/10/40, émetteurs publics (35 % et dérogation de
l'art. 45), dépôts, contrepartie OTC, limite combinée, groupe, OPC, actifs non éligibles, exposition globale
(engagement), emprise, emprunts, liquidités à titre accessoire.

## 6. Traçabilité, déterminisme et sécurité

- Chaque fichier est identifié par son empreinte SHA-256 et **archivé** (`data/archive`) avant traitement. Un
  fichier au même nom et au même contenu n'est jamais retraité. Une nouvelle version d'un fichier crée un nouveau
  jeu de données et l'ancien est conservé (statut « remplacé »).
- Chaque résultat conserve :
  - la version et l'empreinte de la règle ;
  - l'empreinte des données (snapshot) et de l'entrée de calcul (positions enrichies, référentiels, fiche fonds) ;
  - la version du moteur ;
  - la trace complète du calcul et les lignes sources ;
  - une **empreinte du résultat**. À données, règles et moteur identiques, l'empreinte est identique ;
    c'est testé automatiquement.
- Les résultats, snapshots, versions de règles et journaux sont **en ajout seul**. Le journal d'audit est chaîné
  par hachage.
- Fonctionnement **100 % local** : écoute sur `127.0.0.1`, aucune ressource externe (ni CDN ni API), aucun envoi
  de données.

## 7. Exports

- **PDF** : date du contrôle, périmètre, synthèse, points d'attention (dépassements, dépassements indicatifs,
  non calculables), détail par fonds et par catégorie (valeur, limite, marge, statut, source), ratios non
  applicables, annexes (versions de règles et sources, anomalies de données), cartouche de visa.
- **Excel** (7 onglets) : Synthèse, Ratios, Calculs, Données sources, Référentiel réglementaire, Anomalies,
  Métadonnées.
  - Les montants et poids de l'onglet Calculs sont des formules `SUMPRODUCT` qui relisent l'onglet Données
    sources selon les filtres de la règle.
  - Les ratios (`MAX`, `SUMIF`), les limites (référence à l'onglet Référentiel), les marges, les statuts
    recalculés et les compteurs de synthèse (`COUNTIFS`) sont aussi des formules.
  - Une colonne « Écart » compare chaque formule à la valeur du moteur et se colore si l'écart dépasse 1e-9.

## 8. Tests

```bat
.venv\Scripts\activate
python -m pytest
```
Le jeu de données **fictif** `tests/fixtures/fonds_test` (actif net de 100 000 000 €) comporte les résultats
attendus **calculés à la main** et commentés dans `expected.yaml`. Les tests couvrent notamment :
- les formules ;
- les statuts et marges ;
- l'applicabilité à trois états ;
- le versionnement et l'immuabilité des règles ;
- la lecture CSV / XLSX et le mapping ;
- les cas d'erreur (fichier absent, illisible, colonne absente, fonds non identifié, dates incohérentes,
  nouvelle version d'un fichier) ;
- le déterminisme, l'intégrité du journal ;
- le recalcul des formules Excel (si la bibliothèque `formulas` est installée) ;
- les exports et l'interface.

Avant de passer une règle en production, ajouter un cas de test dédié dans ce jeu, avec son calcul manuel.

## 9. Arborescence

```
config/            settings.yaml, mappings/ (profils de fichiers), funds/ (fiches fonds), referentiels/
referentiel/       sources.yaml, rules/ (règles YAML versionnées)
src/opcvm_ratios/
  ingestion/       détection, empreintes, archivage, lecture CSV/XLS/XLSX, profils
  normalization/   mapping, typage, contrôles de cohérence
  rules/           chargement, validation, immuabilité, applicabilité
  engine/          filtres déclaratifs, calculateurs purs, exécution
  control.py       limites, marges, statuts
  referentials.py  émetteurs, groupes, marchés (enrichissement tracé)
  storage/         SQLite (ajout seul) et journal d'audit chaîné
  exports/         PDF (ReportLab) et Excel (XlsxWriter)
  web/             interface FastAPI + gabarits
tests/             unitaires, intégration, jeu de test fictif
```
