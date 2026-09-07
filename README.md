# Template — Annexes de déclaration TVA périodique (Luxembourg)

> ## ▶ Lancer la page de campagne
>
> **Double-clic sur `webapp/OuvrirCampagneTVA` dans ce dossier.**
> La page s'ouvre toute seule à cette adresse, toujours la même :
>
> ### http://127.0.0.1:8743/
>
> *(à mettre en favori dans le navigateur — plus besoin de la chercher)*
>
> Pour arrêter : lien **« Fermer l'application »** en bas de la page.
> Sinon le serveur reste en fond et le lancement suivant se contente de
> rouvrir l'onglet.
>
> **Si le navigateur affiche `ERR_CONNECTION_REFUSED`** : le serveur n'est pas
> démarré. Relance l'app ; le journal `~/campagne_tva_web.log` dit ce qui
> s'est passé. En dernier recours, depuis un terminal :
>
> ```bash
> cd ~/CampagneTVA && python3 webapp/serveur.py
> ```

## Organisation des livrables

Un dossier par société, puis par année, puis par période :

```
dossiers/
  ACME/                       créé par « Ajouter une société… »
    societe.yaml              identité + identifiants eCDF
    factures/  extraction/    sources et lectures figées
    2025/
      Q3/                     livrables de la période
        …-APPENDICES.xlsx
        …-EXCEPTIONS.txt / .json
        …-CASES-ECDF.txt
        …-DECLARE.json        instantané machine
        …-REPLIQUE.pdf        réplique client
        AG0001X…​.xml          fichier eCDF déposable
        _apercu/              écran 2, avant confirmation du prorata
      M08/  ANNUAL/           autres périodes de la même année
```

Les noms de période sont ceux du moteur — `Q3`, `M08`, `ANNUAL` — pour qu'un
dossier porte le même nom que ce que disent les fichiers qu'il contient.

Le **prorata annuel** relit les périodes déjà déclarées de la même année :
`ca_de_l_annee` balaie le dossier de la période *et ses dossiers frères*.
D'où deux règles à ne pas casser : les périodes d'une même année restent
côte à côte sous l'année, et les dossiers préfixés `_` sont ignorés du
balayage — c'est pourquoi les aperçus vivent sous `_apercu/`.


## Où vivent les fichiers

| Rôle | Emplacement |
|---|---|
| **Copie de travail** — la seule qu'on modifie | `~/CampagneTVA` (disque local) |
| Sauvegarde du **code**, versionnée | GitHub — `Loyon-Thomas/workflow-vat` |
| Sauvegarde des **données**, miroir quotidien | pCloud — `.../Workflows/VAT/CampagneTVA-SAUVEGARDE` |

Le dépôt a quitté pCloud le 07/09/2026. `pcloudfs` ne conserve pas le bit
exécutable, impose une autorisation « Accès complet au disque » au binaire
python3, mélange les formes NFC/NFD des accents et peut se démonter en
pleine session — quatre défauts sans conséquence pour une *sauvegarde*, mais
qui rendaient la copie de *travail* fragile. L'ancienne copie est gelée sous
`_TEMPLATE-ANNEXES-TVA-ANCIEN-20260907`, à supprimer quand la nouvelle
organisation aura fait ses preuves.

La sauvegarde est en **sens unique** : `~/CampagneTVA` → pCloud, jamais
l'inverse. Elle tourne chaque jour à 19h00 (agent launchd
`com.thomas.campagnetva.sauvegarde`), rattrapée au réveil si le Mac dormait,
et reportée sans dégât si pCloud n'est pas monté.

```bash
~/CampagneTVA/sauvegarder.sh --essai
```

montre ce qui serait copié sans rien écrire ; sans `--essai`, la sauvegarde
part immédiatement. Journal : `~/campagne_tva_sauvegarde.log`.

Automatisation générique, applicable à n'importe quelle société, pour produire
les annexes d'une déclaration TVA **mensuelle ou trimestrielle** à partir des
**factures** (la comptabilité n'étant pas tenue dans les temps).

## Principe de conception

**Le calcul est du code, la lecture est de l'IA.**
Aucun total, aucun arrondi, aucune agrégation n'est produit par un modèle de
langage. L'IA lit les factures et fige le résultat dans des JSON horodatés ;
le moteur ne fait que contrôler, agréger et mettre en forme. Deux exécutions
sur les mêmes JSON produisent un fichier identique — condition nécessaire pour
qu'un livrable soit défendable en contrôle.

**Rien de spécifique à une société n'est dans le code.** Régime, prorata,
référentiel tiers, format de sortie : tout vit dans `dossiers/<CODE>/*.yaml`.

## Arborescence

```
_TEMPLATE-ANNEXES-TVA/
  config/
    mapping_ecdf.yaml            # cases eCDF — STATUT : A_VALIDER
    societe.template.yaml        # à copier par société
    referentiel.template.yaml    # à copier par société
  src/annexes_tva.py             # moteur
  dossiers/<CODE>/
    societe.yaml
    referentiel.yaml
    factures/                    # PDF déposés bruts, doublons tolérés
    extraction/                  # piste d'audit : 1 JSON par facture
    annexes/                     # classeurs produits
```

## Utilisation

```bash
# 1. Inventaire : dédoublonnage SHA-256, classement natif/scan, extraction texte
#    --factures pointe le dossier client connecté via Cowork (parcours récursif)
python3 src/annexes_tva.py inventaire --dossier dossiers/EXEMPLE-CLIENT \
        --factures ~/Documents/Clients/EXEMPLE-CLIENT/Factures

# 2. Lecture des factures → un <sha12>.json par document dans extraction/
#    natifs : texte disponible dans extraction/textes/ ; scans : vision

# 3. Contrôles + génération
python3 src/annexes_tva.py annexes --dossier dossiers/EXEMPLE-CLIENT
```

L'étape 3 est **interactive**. Le script demande :

```
PERIODE DE DECLARATION
  1) Mensuelle    2) Trimestrielle    3) Annuelle
  Type de declaration [2] : 2
  Annee [2026] : 2025
  Trimestre (1-4) : 3
  -> Quarter 3 2025 : 01/07/2025 au 30/09/2025

DROIT A DEDUCTION
  Source : derniere declaration TVA annuelle deposee.
  Exercice de reference [2024] : 2024
  Prorata de deduction en % (ex. 92) [92] : 92
```

Mode non interactif (planification, tests) : `--periode 2025-Q3 --prorata 92 --exercice 2024`.

### Périmètre : la date d'émission fait foi

Chaque facture est rattachée à une période par sa **date d'émission**. Un document
hors période n'est **pas une erreur** : il est écarté des annexes et inscrit au
**registre de reprise** du rapport d'exceptions, avec sa position (antérieure ou
ultérieure). À vérifier une par une : déjà déclarée sur sa période d'émission, ou
à régulariser.

### Droit à déduction — prorata calculé sur le CA de l'année

Le prorata général de l'article 50 est **calculé sur le chiffre d'affaires de
l'année civile** : CA ouvrant droit à déduction / CA total, arrondi à l'unité
supérieure (directive 2006/112/CE, art. 175 §1).

Le moteur agrège les ventes de la période en cours **et** celles des autres
périodes déjà déclarées de l'exercice, lues dans les instantanés
`-DECLARE.json`. La valeur calculée est proposée, peut être remplacée, et son
origine est tracée sur le livrable.

```
DROIT A DEDUCTION - prorata calcule sur le CA de l'annee 2025
  CA total                 :    311000.00
  CA ouvrant droit         :    251000.00
  (dont 4 autre(s) periode(s) declaree(s) de l'exercice)
  Prorata                  : 0.807074 -> 81 % (arrondi a l'unite superieure)
  Prorata retenu en % [81] :
```

Sur une période infra-annuelle, le CA de l'exercice est partiel : le prorata est
alors marqué **PROVISOIRE — à régulariser sur l'annuelle** dans le rapport et
dans l'instantané.

Numérateur : ventes LU taxables, livraisons intracommunautaires, exportations,
services rendus à des preneurs UE redevables, opérations dont le lieu est hors
UE. Dénominateur : la totalité du chiffre d'affaires, exonérations art. 44
comprises.

## Sorties

| Fichier | Contenu |
|---|---|
| `<nom>.xlsx` | Annexes uniquement : `Detail CA`, `Detail TVA Lux`, `Detail TVA debiteur`, `VAT recovery right` |
| `<nom>-EXCEPTIONS.txt` | Bloquantes, à revoir, registre de reprise hors période |
| `<nom>-CASES-ECDF.txt` | Correspondance vers les cases de la déclaration |

Mise en forme : titre et période fusionnés et centrés sur la largeur du tableau,
en-têtes en gras souligné d'un filet, taux de TVA affichés en **pourcentage**
(`17%`, pas `0.17`), montants en `#,##0.00`, totaux de bloc soulignés d'un filet,
grand total d'un double filet, volets figés sous les en-têtes. Aucun aplat de
couleur.

## Source des factures

`--factures` (ou `societe.yaml: source_factures`) désigne le **dossier client
connecté via Cowork**. Il est parcouru **récursivement** ; les sous-dossiers
commençant par `.` ou `_` et tout fichier non-PDF sont ignorés. Les originaux ne
sont **jamais modifiés ni déplacés** : le moteur ne fait que les lire et les
identifier par empreinte SHA-256.

`--sortie` (ou `societe.yaml: dossier_sortie`) désigne où déposer les trois
livrables ; par défaut `<dossier>/annexes`. Pointer ce paramètre vers le dossier
client permet de livrer directement chez lui.

Ordre de priorité pour les deux : argument de ligne de commande, puis
`societe.yaml`, puis valeur par défaut.

**Rien n'est jamais écrit dans le dossier client.** Les livrables restent dans
l'espace de travail ; leur dépôt éventuel chez le client est un geste manuel,
après solde des exceptions bloquantes.

### Sens achat / vente — trois sources croisées

Le sens est établi en croisant **trois sources indépendantes** :

| Source | Mécanisme | Couvre |
|---|---|---|
| **Chemin** | segments du dossier (`Achats/…`, `Ventes/…`) | arborescence séparée |
| **Identité** | la société est-elle émetteur ou destinataire de la facture, par n° de TVA ou par alias | **dossier unique mélangeant achats et ventes** |
| **Extraction** | sens porté par le JSON de lecture | filet de sécurité |

Il suffit qu'**une** source réponde. **Toute divergence entre deux sources, comme
le silence des trois, est bloquante** (C11) — le moteur ne tranche jamais un
désaccord tout seul.

```
Divergence de sens : chemin='vente', extraction='achat', identite='achat'
Sens indetermine : ni le chemin, ni l'identite des parties, ni l'extraction
                   ne permettent de trancher achat / vente
```

La source « identité » exige `numero_tva` **ou** `alias_societe` renseignés dans
`societe.yaml` ; c'est ce qui rend exploitable un dossier client unique, sans
séparation achats / ventes. L'extraction doit alors porter `emetteur`,
`emetteur_tva`, `destinataire`, `destinataire_tva`.

Trois configurations testées : dossier unique (5 factures, sens établi par
l'identité), arborescence `Achats/` + `Ventes/` (sens établi par le chemin,
divergence détectée sur une facture mal classée), dossier neutre avec parties
inconnues (blocage C11).

### Exigibilité — règle figée, non paramétrable

Rattachement par **date d'émission de la facture**, **identique en amont et en
aval**. Régime des débits : loi TVA du 12.02.1979 modifiée, **art. 24 §1** (par
dérogation à l'art. 21). L'option pour l'imposition d'après les recettes
(**art. 25 §1**) n'est exercée par aucune société du portefeuille. Règle
confirmée par le préparateur pour les deux sens, comptabilité tenue en régime
débits.

## Format d'extraction (une facture = un JSON)

```json
{
  "sens": "achat",
  "emetteur": "Telecom Exemple S.A.", "emetteur_tva": "LU00000004",
  "destinataire": "Exemple Client S.a r.l.", "destinataire_tva": null,
  "tiers": "Telecom Exemple", "tva_tiers": "LU00000004", "pays": "LU",
  "num_facture": "INV/1272637/2025", "date": "2025-08-13",
  "devise": "EUR", "taux_change": 1.0, "total_ttc": 163.80,
  "regime": "achat_lu",
  "source_extraction": "texte",
  "lignes": [
    {"taux": 0.0,  "base": 81.00, "tva": 0.00,  "description": "..."},
    {"taux": 0.17, "base": 70.77, "tva": 12.03, "description": "..."}
  ]
}
```

Régimes admis : `achat_lu`, `exonere_art44`, `acq_intracom`, `service_autoliq`,
`import`, `vente_lu`, `livraison_intracom`, `export`, `hors_ue`.

Les taux sont stockés en décimal (`0.17`) et **affichés en pourcentage** (`17%`).
**La TVA reprise est celle facturée**, pas un produit `base × taux` ; le contrôle
C1 vérifie la cohérence entre les deux dans la tolérance paramétrée.

## Contrôles

| Code | Gravité | Objet |
|---|---|---|
| C0 | bloquant | Extraction absente pour un document inventorié |
| C1 | bloquant | `base × taux ≠ TVA facturée` au-delà de la tolérance |
| C2 | bloquant | Somme des lignes ≠ total TTC de la facture |
| C3 | bloquant | Taux hors {0 ; 3 ; 8 ; 14 ; 17 %} |
| C4 | bloquant | Date hors période → registre de reprise |
| C5 | bloquant | N° de facture absent ou non conforme au format du référentiel |
| C6 | bloquant | Doublon (même tiers, même n°) |
| C7 | bloquant | Devise étrangère sans taux de change documenté |
| C8 | bloquant | Trou dans une série récurrente (fournisseur mensuel/trimestriel) |
| C9 | revue | Signalement de qualification défini au référentiel |
| C11 | bloquant | Sens indéterminé, ou divergence entre les sources de sens |

**Une ligne bloquante n'entre jamais dans les annexes et n'est jamais absorbée
en silence : elle est tracée dans l'onglet `Exceptions`.**

## Limites — à connaître avant usage

1. **Exhaustivité partielle.** En mode « factures seules », une facture jamais
   déposée est indétectable si son fournisseur n'est pas déclaré récurrent au
   référentiel. C8 est le seul filet ; il ne couvre pas les fournisseurs
   ponctuels. Le livrable complet étant soumis au client pour accord avant
   dépôt, cet accord vaut confirmation de complétude du lot retenu. La
   réconciliation annuelle (`reconciliation.py`) est le filet de rattrapage.
2. **Mapping eCDF : deux profils, tous deux vérifiés.**
   `config/mapping_ecdf.yaml` contient un profil par formulaire, choisi selon le
   type de période.
   - `TVA_DECA_2025` (annuelle) — **VALIDE**, relevé sur le formulaire officiel
     2025M1V002.
   - `TVA_DECM_2025` (mensuelle et trimestrielle) — **VALIDE**, relevé sur les
     formulaires officiels 2025 « Return for October 2025 » et « Return for the
     4th quarter of calendar year 2025 ». Numéros identiques sur les deux : un
     seul profil couvre M et Q.

   **Les identifiants eCDF ne sont pas intégralement partagés entre
   formulaires** : les livraisons intracommunautaires de biens sont en case
   **457** au DECM et **013** au DECA. La ventilation de la TVA en amont diffère
   aussi — par origine au DECM (458 / 459 / 460 / 461), par nature de dépense au
   DECA (stock 077 / immobilisations 081 / frais généraux 085). Le moteur ne
   pouvant pas déduire la nature d'une dépense depuis une facture, ces quatre
   postes ressortent explicitement à ventiler à la main sur l'annuelle.

   Tout montant sans case mappée est listé sous « MONTANTS SANS CASE MAPPEE » —
   jamais absorbé.
3. **Aucune qualification TVA.** Le moteur ne tranche ni le lieu de prestation,
   ni l'exonération, ni l'autoliquidation, ni la déductibilité. Le `regime` est
   une donnée d'entrée, décidée par le préparateur.
4. **Exigibilité sur les débits** (date de facture) par défaut — hypothèse
   paramétrable par société, non vérifiée dossier par dossier.

## Test de non-régression

Le jeu `dossiers/EXEMPLE-CLIENT` (5 factures d'août 2025, 2 natives + 3 scans) sert de
cas de référence. Injection des quatre erreurs réellement constatées dans
l'annexe 2025 → les quatre sont rejetées : n° client saisi comme n° de facture
(C5), n° de facture à 11 chiffres (C5), TVA incohérente (C1 + C2), facture hors
période (C4).

---

# Module de réconciliation post-comptabilisation

`src/reconciliation.py` — support de la **déclaration TVA annuelle
récapitulative**, qui est théoriquement le cumul des déclarations périodiques
de l'exercice. Le module vérifie que ce cumul tient, puis le confronte à la
comptabilité une fois celle-ci rattrapée.

```bash
python3 src/reconciliation.py --dossier dossiers/EXEMPLE-CLIENT --annee 2025 \
        --gl ~/exports/GL-2025.xlsx --feuille GL
```

L'export comptable peut être `.xlsx` ou `.csv`. Les colonnes sont décrites dans
`config/mapping_gl.yaml` (un profil par logiciel, avec alias d'en-têtes).

## Axe A — les périodiques cumulées font-elles l'annuelle ?

Chaque exécution de `annexes_tva.py` dépose un instantané machine
`…-DECLARE.json` : lignes retenues, période, prorata, exceptions non soldées,
registre de reprise. C'est le socle de la réconciliation, et la piste d'audit.

| Code | Gravité | Objet |
|---|---|---|
| R1 | bloquant | Aucune déclaration produite pour une période attendue |
| R2 | bloquant | Exceptions bloquantes non soldées à la génération d'une période |
| R3 | bloquant | Même facture déclarée dans plusieurs périodes |
| R4 | bloquant | Facture inscrite au registre de reprise et jamais reprise |
| R5 | revue | Même numéro de facture à plusieurs dates chez un même tiers |

## Axe B — déclaré vs comptabilité

Rapprochement au niveau facture, en cascade à trois niveaux, chaque pièce et
chaque facture n'étant consommée qu'une fois :

1. **certain** — le numéro de facture apparaît dans le libellé de la pièce
2. **probable** — montants concordants et dates proches (± 7 jours)
3. **faible** — montants concordants seuls

Quatre catégories exhaustives :

| Catégorie | Signification | Traitement |
|---|---|---|
| Concordant | Même facture, mêmes montants | — |
| **Écart de montant** | Même facture, montants divergents | Trancher : déclaration ou comptabilité |
| **Déclaré non comptabilisé** | Déclaré à l'administration, absent de la compta | Pièce non comptabilisée, ou référence non atteinte par le rapprochement |
| **Comptabilisé non déclaré** | En compta, dans aucune déclaration | **Manquement déclaratif** — TVA jamais portée à l'administration. Rectificative ou régularisation |

## Sorties

| Fichier | Contenu |
|---|---|
| `<CODE>-RECONCILIATION-<annee>.xlsx` | Synthèse, anomalies de cumul, écarts, concordants, déclaré non comptabilisé, comptabilisé non déclaré, cumul par facture |
| `<CODE>-RECONCILIATION-<annee>-SYNTHESE.txt` | Rapport de lecture, montants en clair, limites |

## Limites

**Autoliquidation.** La TVA autoliquidée est due en aval **et** déductible en
amont : elle figure des deux côtés. Le formulaire officiel le confirme —
`076 = 046 + 056 + 407 + 410 + 768 + 227`, où 056, 407 et 410 sont
respectivement les acquisitions intracommunautaires, les importations et les
services reçus autoliquidés. Le contrôle C2 en tient compte — sur ces
régimes, le total de la facture est égal à la base, le fournisseur ne facturant
pas de TVA.

Le rapprochement s'appuie sur le numéro de facture lu dans le libellé de la
pièce comptable, puis à défaut sur les montants et la date. Un rapprochement de
confiance **faible** (montants seuls) peut associer deux opérations distinctes
de même montant : ces lignes portent leur niveau de confiance et doivent être
vérifiées. Le module **ne requalifie aucune opération** et ne juge pas si un
écart provient de la déclaration ou de la comptabilité.

## Tests

### `tests/jeu-2025/` — jeu complet fictif, toutes catégories

31 factures TESTCO S.à r.l., arborescence `Achats/` + `Ventes/`, prorata 77 %.
Couvre achats LU aux quatre taux, exonérations art. 44, acquisitions
intracommunautaires, services reçus UE et pays tiers, importations, ventes LU
aux quatre taux, livraisons intracommunautaires, exportations, services hors
UE. Sept défauts injectés, vérité terrain dans `ATTENDU.md`. Régénérer :
`python3 tests/jeu-2025/generer.py`.

### `tests/fixture-reconciliation/` — douze factures, sept défauts injectés, vérité
terrain documentée dans `ATTENDU.md`. Tous détectés.
