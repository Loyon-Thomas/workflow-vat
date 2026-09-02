# Annexes de déclaration TVA — spécification du workflow automatisé

Version 8.0 — 02/09/2026
Dépôt : https://github.com/Loyon-Thomas/workflow-vat

---

## 1. Cadrage

Automatisation générique, applicable à n'importe quelle société, de la production des annexes d'une déclaration TVA **mensuelle, trimestrielle ou annuelle** luxembourgeoise, puis de leur **réconciliation avec la comptabilité** une fois celle-ci rattrapée.

**Contrainte structurante :** la comptabilité n'étant pas tenue dans les temps, le travail s'effectue **sur base des factures**.

| Paramètre | Décision |
|---|---|
| Source | Dossier client connecté via Cowork — arborescence séparée ou dossier unique |
| Dépôt des livrables | Espace de travail — **rien n'est écrit dans le dossier client** |
| Sorties | Annexes (.xlsx) + exceptions et correspondance eCDF (.txt) + instantané (.json) |
| Exigibilité | Débits, date d'émission, **identique en amont et en aval** — règle figée |
| Prorata | **Calculé sur le CA de l'année civile**, art. 50 |
| Complétude | Accord du client sur le livrable complet avant dépôt |

---

## 2. Principe

**Le calcul est du code, la lecture est de l'IA.** Aucun total, aucun arrondi, aucune agrégation n'est produit par un modèle de langage. Deux exécutions sur les mêmes JSON produisent un fichier identique.

**Rien de spécifique à une société n'est dans le code** : `societe.yaml`, `referentiel.yaml`, `config/mapping_gl.yaml`, `config/mapping_ecdf.yaml`.

---

## 3. Chaîne périodique — `src/annexes_tva.py`

Inventaire (parcours récursif, dédoublonnage SHA-256, indice de sens, natif/scan) → lecture par Claude → saisie de la période → périmètre par date d'émission → contrôles → génération.

### 3.1 Sens achat / vente — trois sources croisées

Chemin (`Achats/`, `Ventes/`), identité des parties (couvre le **dossier unique**), sens porté par l'extraction. Une source suffit ; toute divergence, comme le silence des trois, est bloquante (C11).

### 3.2 Périmètre — date d'émission

Loi TVA du 12.02.1979 modifiée : art. 21 (fait générateur), **art. 24 §1** (exigibilité à l'émission de la facture), art. 25 §1 (option encaissements, non exercée). **Confirmé par le préparateur pour les deux sens, comptabilité en régime débits.** Les formulaires portent ce choix en case **204** (on sales) / **205** (on payments received).

Hors période → **registre de reprise**, avec position antérieure ou ultérieure.

### 3.3 Prorata — calculé sur le CA de l'année

Prorata général de l'**article 50** : CA ouvrant droit / CA total, **arrondi à l'unité supérieure** (directive 2006/112/CE, art. 175 §1).

Le moteur agrège les ventes de la période en cours **et** celles des autres périodes déjà déclarées de l'exercice, lues dans les instantanés `-DECLARE.json`. La valeur calculée est proposée, peut être remplacée, et son origine est tracée sur le livrable.

- Numérateur : ventes LU taxables, livraisons intracommunautaires, exportations, services rendus à des preneurs UE redevables, opérations dont le lieu est hors UE.
- Dénominateur : la totalité du chiffre d'affaires, exonérations art. 44 comprises.

Sur une période infra-annuelle, le CA de l'exercice est partiel : le prorata est marqué **PROVISOIRE — à régulariser sur l'annuelle**.

### 3.4 Autoliquidation

La TVA autoliquidée est **due en aval et déductible en amont** : elle figure des deux côtés. Les formulaires le confirment — `076 = 046 + 056 + 407 + 410 + 768 + 227`. Le contrôle C2 en tient compte : sur ces régimes le total de la facture est égal à la base.

### 3.5 Contrôles

| Code | Gravité | Objet |
|---|---|---|
| C0 | bloquant | Extraction absente pour un document inventorié |
| C1 | bloquant | `base × taux ≠ TVA facturée` au-delà de 0,02 € |
| C2 | bloquant | Somme des lignes ≠ total TTC (base seule en autoliquidation) |
| C3 | bloquant | Taux hors {0 ; 3 ; 8 ; 14 ; 17 %} |
| C5 | bloquant | N° de facture absent ou non conforme au référentiel |
| C6 | bloquant | Doublon (même tiers, même n°) |
| C7 | bloquant | Devise étrangère sans taux de change documenté |
| C8 | bloquant | Trou dans une série récurrente |
| C9 | revue | Signalement de qualification défini au référentiel |
| C11 | bloquant | Sens indéterminé, ou divergence entre les sources de sens |

---

## 4. Chaîne annuelle — `src/reconciliation.py`

**Axe A** — les périodiques cumulées font-elles l'annuelle ? R1 période manquante, R2 exceptions non soldées, R3 facture sur plusieurs périodes, R4 registre de reprise jamais repris, R5 même numéro à plusieurs dates.

**Axe B** — déclaré vs comptabilité. Rapprochement au niveau facture, cascade certain (n° dans le libellé) → probable (montants + date ± 7 j) → faible (montants seuls). Quatre catégories : concordant, écart de montant, déclaré non comptabilisé, **comptabilisé non déclaré** (manquement déclaratif, TVA jamais portée à l'administration).

Export comptable `.xlsx` ou `.csv`, colonnes décrites dans `config/mapping_gl.yaml`.

---

## 5. Correspondance eCDF — deux profils, tous deux vérifiés

`config/mapping_ecdf.yaml`, profil choisi selon le type de période. Formulaires archivés dans `formulaires-officiels/`.

| Profil | Périodes | Statut | Source |
|---|---|---|---|
| `TVA_DECA_2025` | annuelle | **VALIDE** | Formulaire officiel 2025M1V002, *Return for year 2025* |
| `TVA_DECM_2025` | mensuelle, trimestrielle | **VALIDE** | Formulaires officiels 2025, *Return for October 2025* et *Return for the 4th quarter of calendar year 2025* — numéros identiques sur les deux |

### Les identifiants eCDF ne sont pas intégralement partagés entre formulaires

| Poste | DECM | DECA |
|---|---|---|
| Livraisons intracommunautaires de biens | **457** | **013** |

La ventilation de la TVA en amont diffère structurellement :

- **DECM — par origine** : 458 facturée par assujettis, 459 acquisitions intracommunautaires, 460 importations, 461 autoliquidation
- **DECA — par nature de dépense** : stock (077/078/079/404), immobilisations (081/082/083/405), frais généraux (085/086/087/406)

Le moteur ne peut pas déduire la nature d'une dépense d'une facture. Sur l'annuelle, ces quatre postes ressortent sous « MONTANTS SANS CASE MAPPÉE » avec la ventilation attendue indiquée — décision du préparateur, pas du calcul.

### Cases corrigées en cours de construction

Le mapping initial, bâti sur une lecture indirecte du DECM 2020, comportait **huit erreurs sur les cases de taxe** :

| Poste | Avant | Corrigé |
|---|---|---|
| Ventes LU 17 % | 701 / 040 | 701 / **702** |
| Ventes LU 14 % | 703 / 042 | 703 / **704** |
| Ventes LU 8 % | 705 / 417 | 705 / **706** |
| Ventes LU 3 % | 031 / 452 | 031 / **040** |
| Acquisitions intracom 17 % | 711 / 054 | 711 / **712** |
| Importations 17 % | 721 / 059 | 721 / **722** |
| Services reçus UE 17 % | 741 / 431 | 741 / **742** |
| Services reçus tiers 17 % | 751 / 441 | 751 / **752** |

Cases ajoutées : 012 CA global, 454 total ventes, 021 exonérations, 022 CA taxable, 037/046 total taxable, 013 ou 457 livraisons intracommunautaires, 016 exonérations art. 44, 423 services rendus à preneurs UE, 019 opérations lieu à l'étranger, 436/462 et 463/464 sous-totaux services reçus, 458/459/460/461 ventilation amont DECM, 076 total taxe due, 094/095/097 non déductible.

---

## 6. Jeux d'essai

### `tests/jeu-2025/` — jeu complet fictif, toutes catégories

31 factures TESTCO S.à r.l., arborescence séparée, prorata calculé à **81 %** (251 000 / 311 000 = 0,807074, arrondi supérieur). Couvre achats LU aux quatre taux, exonérations art. 44, acquisitions intracommunautaires, services reçus UE et pays tiers, importations, ventes LU aux quatre taux, livraisons intracommunautaires, exportations, services hors UE.

Sept défauts injectés, sept détectés. Contrôle de cohérence : `076 = 046 + 056 + 407 + 410 = 14 380 + 1 241 + 884 + 1 938 = 18 443`. ✓

### `tests/fixture-reconciliation/`

12 factures, 7 défauts, vérité terrain documentée. Le doublon inter-périodes est attrapé deux fois : par R3 au cumul et par un écart valant exactement la facture dupliquée.

---

## 7. Cartographie des risques

| Risque | Probabilité | Impact | Mitigation | Couverture |
|---|---|---|---|---|
| **Facture jamais déposée** | Moyenne | TVA perdue ou base sous-déclarée | C8, accord client, réconciliation annuelle | **Différée** : rattrapée à la réconciliation, pas avant le dépôt |
| **Ventilation amont annuelle** | Certaine | Cases 077/081/085 à remplir à la main | Postes listés avec la ventilation attendue | Signalée |
| **Sens mal établi** | Moyenne | Achat traité en vente | C11, trois sources croisées | Bonne |
| **Extraction erronée** | Moyenne | Montant faux | C1 + C2 | Bonne |
| **Métadonnées fausses** | Élevée | Annexe non rapprochable | Référentiel + C5 | Bonne |
| **Prorata provisoire** | Certaine en infra-annuel | Déduction à régulariser | Mention PROVISOIRE tracée | Signalée |
| **Rapprochement faible erroné** | Faible | Écart masqué | Niveau de confiance par ligne | Signalée |
| **Cut-off** | Élevée en mensuel | Double comptage ou omission | Registre de reprise + R4 | Bonne |
| **Formulaire eCDF modifié** | Certaine à terme | Report en case erronée | Profils versionnés, formulaires archivés | Organisationnelle |
| **Option art. 25 exercée par un client** | Faible | Rattachement faux | Aucune — règle figée | **Nulle** |
| **Erreur de qualification TVA** | Faible | Élevé | Aucune — `regime` est une donnée d'entrée | **Nulle** |

---

## 8. Suites

1. Renseigner `numero_tva` dans chaque `societe.yaml` — fiabilise C11.
2. Compléter `config/mapping_gl.yaml` au fil des logiciels comptables rencontrés.
3. Report automatique du registre de reprise sur la période suivante.
4. Revérifier les profils eCDF à chaque millésime de formulaire.

---

## Sources

- Formulaires officiels eCDF archivés dans `formulaires-officiels/` : annuel 2025M1V002, mensuel 2025, trimestriel 2025
- Loi TVA du 12 février 1979 modifiée — art. 21, 24 §1, 25 §1, 50
- Directive 2006/112/CE — art. 175 §1 (arrondi du prorata)
