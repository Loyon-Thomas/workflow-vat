# Jeu d'essai 2025 — TESTCO S.à r.l.

**Données entièrement fictives.** Destinées au test de bout en bout des deux
modules. Les extractions JSON sont produites par `generer.py`, pas par une
lecture de document : le jeu éprouve la chaîne de contrôle et d'agrégation, pas
la qualité de l'extraction.

Régénérer : `python3 tests/jeu-2025/generer.py`

## Composition

31 factures, dossier client à arborescence séparée `Achats/` + `Ventes/`, avec
sous-dossiers trimestriels. Prorata général **calculé sur le CA de l'année** : 251 000,00 / 311 000,00 =
0,807074 → **81 %** après arrondi à l'unité supérieure.

| Catégorie | Pièces | Base EUR | TVA EUR |
|---|---|---|---|
| Achats LU 17 % | 5 | 15 090,00 | 2 565,30 |
| Achats LU 14 % | 2 | 1 440,00 | 201,60 |
| Achats LU 8 % | 1 | 1 200,00 | 96,00 |
| Achats LU 3 % | 2 | 750,00 | 22,50 |
| Achats exonérés art. 44 | 2 | 2 420,00 | 0,00 |
| Acquisitions intracommunautaires | 2 | 7 300,00 | 1 241,00 |
| Services reçus UE (autoliquidation) | 2 | 9 000,00 | 1 530,00 |
| Services reçus pays tiers (autoliquidation) | 1 | 2 400,00 | 408,00 |
| Importations | 1 | 5 200,00 | 884,00 |
| Ventes LU 17 / 14 / 8 / 3 % | 6 | 94 000,00 | 14 380,00 |
| Ventes exonérées art. 44 | 1 | 60 000,00 | 0,00 |
| Livraisons intracommunautaires | 2 | 62 000,00 | 0,00 |
| Exportations | 2 | 42 000,00 | 0,00 |
| Services hors UE | 2 | 53 000,00 | 0,00 |

Chiffre d'affaires total **311 000,00** — TVA collectée **14 380,00**.

## Défauts injectés et détection attendue

| Défaut | Détection |
|---|---|
| 2 fichiers copiés à l'octet près | Écartés à l'inventaire par empreinte SHA-256 |
| 1 facture régénérée (mêmes données, octets différents) | Passe l'empreinte, **rejetée en C6** au trimestre 2 |
| 3 pièces sans couche texte | Classées « scan », lecture vision requise |
| A2507 déclarée, absente de la comptabilité | **Déclaré non comptabilisé ×1** |
| A2516 comptabilisée 1 700,00 / 289,00 au lieu de 1 750,00 / 297,50 | **Écart** −50,00 / −8,50 |
| A2519 et V2514 comptabilisées, jamais déclarées | **Comptabilisé non déclaré ×2** — TVA 1 662,60 |
| Trimestre 2 généré avec 1 exception bloquante | **R2** |

## Résultats obtenus

**Chaîne périodique** — 4 trimestres + 1 annuelle :

```
Q1 : 10 lignes, 0 bloquante, TVA amont 1 071,50
Q2 :  9 lignes, 1 bloquante (C6 doublon), TVA amont 2 352,80
Q3 :  6 lignes, 0 bloquante, TVA amont 1 505,00
Q4 :  6 lignes, 0 bloquante, TVA amont 2 002,10
Annuelle : 31 lignes, TVA amont 6 931,40 | déductible 5 614,43 | non déductible 1 316,97
```

Cumul des trimestres = 6 931,40 = annuelle. **Cohérent.**

Sur les trimestres, le prorata est marqué PROVISOIRE : le CA de l'exercice n'y
est connu qu'en partie.

**Correspondance eCDF annuelle** (profil `TVA_DECA_2025`, statut VALIDE) :

| | Base | Taxe |
|---|---|---|
| Ventes LU 17 % (701 / 040) | 75 000,00 | 12 750,00 |
| Ventes LU 14 % (703 / 042) | 6 000,00 | 840,00 |
| Ventes LU 8 % (705 / 417) | 8 000,00 | 640,00 |
| Ventes LU 3 % (031 / 452) | 5 000,00 | 150,00 |
| Livraisons intracommunautaires (457) | 62 000,00 | — |
| Exportations (014) | 42 000,00 | — |
| Acquisitions intracom (051 / 056, 711 / 054) | 7 300,00 | 1 241,00 |
| Services reçus (409 / 410) | 11 400,00 | 1 938,00 |
| — dont UE (741 / 431) | 9 000,00 | 1 530,00 |
| — dont pays tiers (751 / 441) | 2 400,00 | 408,00 |
| Importations (065 / 407, 721 / 059) | 5 200,00 | 884,00 |
| Chiffre d'affaires global (012) | 311 000,00 | |
| Exonérations (021) | 217 000,00 | |
| Chiffre d'affaires taxable (022) | 94 000,00 | |
| Exonérations art. 44 (016) | 60 000,00 | |
| Opérations lieu hors UE (019) | 53 000,00 | |
| **Total taxe due (076 → 103)** | | **18 443,00** |
| **TVA en amont déductible (102 → 104)** | | **5 614,43** |
| **Solde à payer (105)** | | **12 828,57** |

Contrôle de cohérence avec la formule du formulaire :
`076 = 046 + 056 + 407 + 410 = 14 380,00 + 1 241,00 + 884,00 + 1 938,00 = 18 443,00`. ✓

Sans case mappée, signalés explicitement : ventes exonérées art. 44
(60 000,00) et services hors UE (53 000,00).

**Réconciliation 2025 :**

```
4 periodes declarees, 31 factures cumulees, 1 anomalie de cumul (R2)
concordants 29 | ecarts 1 | declare non compta 1 | COMPTA NON DECLARE 2
TVA jamais declaree : 1 662,60
```

Conforme à la vérité terrain sur les sept défauts.

## Défaut du moteur révélé par ce jeu

Avant ce test, la TVA autoliquidée n'était comptée qu'en amont. Elle est
pourtant **due en aval et déductible en amont** : elle figure des deux côtés.
Le total TVA en aval était sous-évalué de 4 063,00 et le solde de déclaration
d'autant. Corrigé, et confirmé depuis par le formulaire officiel :
`076 = 046 + 056 + 407 + 410 + 768 + 227`.

C'est précisément ce qu'un jeu couvrant toutes les catégories devait faire
apparaître : aucun des jeux précédents ne contenait d'autoliquidation.

## Ventilation de la TVA en amont — DECM

Les périodiques utilisent la ventilation par origine, absente du formulaire
annuel :

| Case | Poste | Q4 2025 |
|---|---|---|
| 458 | TVA facturée par des assujettis | 2 002,10 |
| 459 | TVA sur acquisitions intracommunautaires | 0,00 |
| 460 | TVA sur importations | 0,00 |
| 461 | TVA autoliquidée (points II.E et F) | 0,00 |
| 093 | Total TVA en amont | 2 002,10 |
| 095 | Part non déductible (art. 50) | 380,40 |
| 102 | TVA en amont déductible | 1 621,70 |

Sur l'annuelle, ces quatre postes ressortent sous « MONTANTS SANS CASE MAPPÉE »
avec l'indication de la ventilation attendue (stock / immobilisations / frais
généraux) : le moteur ne peut pas déduire la nature d'une dépense d'une facture.
