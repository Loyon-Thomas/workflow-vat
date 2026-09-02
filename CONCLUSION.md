# Conclusion — état de l'automatisation des annexes TVA

02/09/2026 — version 8 des scripts, dépôt `workflow-vat`

---

## 1. Ce que fait la version actuelle

### Chaîne périodique — `src/annexes_tva.py` (830 lignes)

**Ingestion.** Parcours récursif du dossier client connecté via Cowork. Dédoublonnage par empreinte SHA-256. Classement natif / scan par test d'extractibilité du texte. Les originaux ne sont ni modifiés ni déplacés ; aucune écriture dans le dossier client.

**Périmètre.** Période demandée à l'exécution (mensuelle, trimestrielle, annuelle). Rattachement par **date d'émission**, règle figée dans le code avec sa référence légale — art. 24 §1 de la loi du 12.02.1979, régime des débits, identique en amont et en aval. Tout document hors période est écarté et inscrit au registre de reprise, avec sa position antérieure ou ultérieure.

**Sens achat / vente.** Trois sources croisées : segments du chemin, identité des parties (couvre le dossier unique sans séparation), sens porté par l'extraction. Une source suffit ; toute divergence est bloquante.

**Normalisation.** Référentiel tiers par société : alias, numéro de TVA, format attendu du numéro de facture, libellé standard ou règles conditionnelles, taux attendus, périodicité.

**Contrôles.** Dix contrôles, dont neuf bloquants : cohérence arithmétique base × taux, somme des lignes contre total facture (base seule en autoliquidation), taux admis, format et unicité du numéro, devise étrangère documentée, trou dans une série récurrente, cohérence du sens. Aucune ligne bloquante n'entre dans les annexes et aucune n'est absorbée en silence.

**Prorata.** Calculé sur le chiffre d'affaires de l'année civile, art. 50, arrondi à l'unité supérieure. Le moteur agrège les ventes de la période courante et celles des autres périodes déjà déclarées. Marqué PROVISOIRE en infra-annuel.

**Autoliquidation.** Traitée des deux côtés : due en aval, déductible en amont. Acquisitions intracommunautaires, services reçus UE, services reçus pays tiers, importations.

**Sorties.** Classeur au format existant (quatre onglets, titre centré, taux en pourcentage), rapport d'exceptions, correspondance eCDF, instantané machine `-DECLARE.json` qui sert de piste d'audit et de socle à la réconciliation.

**Correspondance eCDF.** Deux profils vérifiés sur formulaires officiels 2025, archivés dans `formulaires-officiels/` : `TVA_DECA_2025` pour l'annuelle, `TVA_DECM_2025` pour les périodiques. Tout montant sans case mappée est listé explicitement.

### Chaîne annuelle — `src/reconciliation.py` (496 lignes)

**Axe A — cohérence interne.** Les périodiques cumulées font-elles l'annuelle ? Cinq contrôles : période manquante, exceptions non soldées, facture déclarée sur plusieurs périodes, registre de reprise jamais repris, même numéro à plusieurs dates.

**Axe B — déclaré contre comptabilité.** Rapprochement au niveau facture, cascade à trois niveaux de confiance. Quatre catégories exhaustives : concordant, écart de montant, déclaré non comptabilisé, comptabilisé non déclaré — cette dernière étant le manquement déclaratif proprement dit.

### Ce qui est éprouvé

31 factures fictives couvrant toutes les catégories, sept défauts injectés, sept détectés. Douze factures pour la réconciliation, sept défauts, tous détectés. Sur les cinq vraies factures le client de reference d'août 2025 : bases et taux extraits exacts à 100 %, trois erreurs de métadonnées détectées dans l'annexe existante.

---

## 2. Zones grises et points à développer

### [Fait] Ce que l'outil ne couvre pas

**La qualification TVA.** Le régime de chaque opération est une donnée d'entrée, pas une déduction du moteur. Lieu de la prestation, exonération art. 44, autoliquidation, déductibilité d'une dépense mixte : décisions humaines, non automatisables en l'état.

**La ventilation de la TVA en amont sur l'annuelle.** Le formulaire annuel ventile par nature de dépense — stock, immobilisations, frais généraux. Cette nature ne se lit pas sur une facture. Quatre postes ressortent donc à remplir à la main, avec la ventilation attendue indiquée.

**L'exhaustivité avant dépôt.** En mode « factures seules », une facture jamais déposée reste indétectable si son fournisseur n'est pas déclaré récurrent. Le manquement est rattrapé à la réconciliation annuelle, pas avant. Entre le dépôt et la réconciliation, l'exposition est entière.

### [Fait] Ce qui n'a pas été mesuré

**La qualité d'extraction sur documents réels.** Les jeux d'essai valident la chaîne de contrôle et d'agrégation, pas la lecture. Aucun test contre un corpus étiqueté. Les seules factures réelles testées sont les cinq d'août 2025.

**Le comportement à volume.** Aucun dossier de plus de 33 documents n'a été traité. Rien n'indique de limite, rien ne la démontre non plus.

### [Fait] Ce qui reste à paramétrer

`numero_tva` n'est renseigné dans aucun `societe.yaml` — la détection du sens s'appuie aujourd'hui sur les alias de dénomination, moins sûrs. `config/mapping_gl.yaml` ne contient qu'un profil d'export comptable. Le référentiel tiers n'existe que pour EXEMPLE-CLIENT.

### [Avis] Développements par ordre d'utilité

1. **Registre de reprise automatique.** Une facture écartée pour cut-off doit réapparaître d'elle-même sur la période suivante. Aujourd'hui c'est une lecture manuelle du rapport. Faible effort, risque évité élevé.
2. **Corpus de référence étiqueté.** Cinquante factures réelles, extractions vérifiées à la main, rejouées à chaque évolution. C'est le seul moyen de mesurer la qualité d'extraction et de détecter une régression.
3. **Détection du régime assistée.** Le pays du fournisseur, son numéro de TVA et la mention portée sur la facture suffisent à proposer un régime. Proposer, pas décider : le préparateur valide.
4. **Gouvernance du mapping eCDF.** Les formulaires changent chaque année. Une vérification annuelle datée, tracée dans le profil.

### [Hypothèse] Ce qui reste incertain

L'option pour l'imposition d'après les recettes (art. 25) n'est exercée par aucune société du portefeuille aujourd'hui. Si un client l'exerce, le rattachement par date d'émission devient faux pour lui, et la correction n'est pas un paramètre : elle exige une source de dates de paiement que le dossier de factures ne contient pas.

---

## 3. Intérêt d'une automatisation poussée avec Claude Code

### [Fait] Où se situe réellement le goulot

Sur les six étapes de la chaîne, cinq sont déjà automatisées et déterministes. **Une seule reste manuelle : la lecture des factures.** C'est là, et nulle part ailleurs, qu'une automatisation supplémentaire produit du gain.

### [Avis] Ce que Claude Code changerait

Il permettrait d'exécuter la chaîne complète sans intervention : parcourir le dossier, lire chaque facture, écrire les JSON, lancer les contrôles, produire les livrables, et ne revenir vers l'humain que sur les exceptions. En lot sur plusieurs sociétés, sur planification.

Le principe de conception tient toujours : les calculs restent du code Python déterministe et rejouable. Claude Code orchestre et lit, il ne calcule pas.

### [Avis] Conditions à respecter

**Périmètre strict.** Claude Code sur l'étape de lecture et l'orchestration. Pas sur les totaux, pas sur la qualification, pas sur le dépôt.

**Corpus de régression d'abord.** Sans jeu étiqueté rejoué à chaque exécution, un changement de modèle peut dégrader l'extraction sans que rien ne le signale. C'est le prérequis, pas une amélioration ultérieure.

**Économie à mesurer.** Le coût par facture dépend du nombre de documents et de la proportion de scans. À volume de portefeuille, les fournisseurs récurrents à mise en page stable — télécoms, bailleurs, prestataires administratifs — se traitent par gabarit déterministe pour une fraction du coût. Réserver la lecture par modèle à la queue de distribution.

**Rien de non réversible.** Aucune écriture dans le dossier client, aucun dépôt automatique, aucune suppression. L'accord du client avant dépôt reste le point de contrôle humain.

### [Avis] Recommandation

**Oui, mais sur l'étape de lecture uniquement, et après avoir constitué le corpus de référence.**

Le reste de la chaîne n'a rien à gagner : elle est déjà automatique, déterministe et testée. Automatiser davantage y ajouterait de la surface d'erreur sans réduire de charge.

Le gain réel se chiffre en heures de lecture évitées par campagne. Il dépend directement du nombre de sociétés et du volume de factures par période — deux données que je n'ai pas. Sans elles, l'intérêt reste qualitatif, et je ne le chiffre pas.
