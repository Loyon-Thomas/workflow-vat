# Journal des décisions — outillage TVA

Décisions arrêtées et prochaines étapes. Le détail des essais et des impasses
n'y figure pas volontairement : seul ce qui engage la suite est conservé.

Dernière mise à jour : 07/09/2026 (bascule sur disque local)

---

## Décisions arrêtées

### Principe général
- **Le calcul reste dans `src/annexes_tva.py` et `src/reconciliation.py`.** Tout
  le reste (lanceur, page web, réplique PDF, XML) orchestre, lit et affiche.
  Aucune arithmétique TVA n'est refaite ailleurs.
- Les modules périphériques rappellent `annexes_tva.agreger` avec les données de
  l'instantané `-DECLARE.json` plutôt que de recalculer.

### Emplacement du dépôt
- **La copie de travail vit sur le disque local, `~/CampagneTVA`** (07/09/2026).
  `pcloudfs` ne conserve pas le bit exécutable, exige « Accès complet au
  disque » pour le binaire python3, mélange NFC/NFD dans les noms de fichiers
  et peut se démonter en session. Quatre pannes sur huit de la mise au point
  venaient de là.
- pCloud devient une **sauvegarde en sens unique**, miroir strict, quotidienne
  à 19h00 (`sauvegarder.sh` + agent launchd), vers
  `.../Workflows/VAT/CampagneTVA-SAUVEGARDE`. Reportée sans dégât si le
  volume n'est pas monté. Jamais lue vers le local.
- Le **code** reste versionné sur GitHub ; pCloud couvre surtout ce que
  `.gitignore` exclut, à commencer par `dossiers/`.
- `config/ecdf.yaml` (matricule, RCS, TVA du cabinet) est désormais dans
  `.gitignore` ; `config/ecdf.template.yaml` est versionné à sa place.
- Le lanceur teste la lecture du dépôt avant de démarrer et nomme le binaire
  python3 à autoriser au lieu de se refermer en silence.

### Lecture des factures
- Faite par **Claude Code en mode non interactif** (`claude -p`), sur
  l'abonnement existant — pas de clé API ni de facturation séparée.
- **C'est le code, pas le modèle, qui écrit sur le disque** : la réponse est
  validée (JSON bien formé, champs requis, régime admis) avant écriture.
- **Repli en cas de doute** : aucun JSON n'est écrit, ce qui déclenche le
  contrôle C0 bloquant, plus une note `A_VERIFIER-<sha12>.txt` expliquant
  pourquoi. Jamais de valeur devinée.

### Modifications du moteur (validées diff par diff)
Trois ajouts purement additifs à `annexes_tva.py`, aucun calcul touché :
1. écriture d'un `-EXCEPTIONS.json` (copie structurée du rapport texte) ;
2. champ `sens_origine` dans les lignes (chemin / identité / extraction) ;
3. détail du prorata dans `-DECLARE.json` (CA total, CA ouvrant droit, brut,
   proposé, autres périodes déclarées).

### Organisation des livrables
- **`dossiers/<CODE>/<ANNEE>/<PERIODE>/`**, période nommée comme le moteur
  (`Q3`, `M08`, `ANNUAL`). Décidé le 07/09/2026.
- Aperçus de l'écran 2 sous `<PERIODE>/_apercu/` : le préfixe `_` les tient
  hors du balayage du prorata annuel.
- **Deux diffs validés sur `annexes_tva.py`** (fonction `ca_de_l_annee`,
  aucun calcul touché) :
  1. le balayage des instantanés couvre aussi les dossiers frères de
     l'année — sans quoi, en arborescence imbriquée, chaque période ne se
     verrait qu'elle-même et le prorata serait faux, silencieusement ;
  2. suppression du test `os.path.isdir(dossier_sortie)`. La fonction est
     appelée AVANT que le dossier de sortie soit créé : le test faisait
     manquer toutes les périodes sœurs à la première génération d'une
     période. `glob` sur un chemin inexistant renvoie `[]`, le test
     n'apportait rien.
- Cinq scénarios vérifiés : à plat, imbriqué dossier présent, imbriqué
  dossier absent, ancêtre préfixé `_`, aucun antécédent.

### Fiche société
- La liste déroulante de l'écran 1 est alimentée par `dossiers/*/societe.yaml`.
  Le bouton « Ajouter une société… » écrit une fiche à partir du gabarit
  versionné, commentaires conservés.
- Champs obligatoires = les trois identifiants du bloc `<Declarer>` d'eCDF :
  matricule (11 ou 13 chiffres), RCS (`B123456` ou `NE`), n° de TVA (8
  chiffres sans `LU`, ou `NE`).
- **Les règles de forme ne sont pas réécrites** : le formulaire appelle les
  normalisateurs d'`ecdf_xml`. Une fiche acceptée ne peut pas produire un
  XML rejeté.
- **Vide ≠ NE** : les normalisateurs traduisent une valeur absente en `NE`,
  ce qui est juste pour le schéma mais faux pour une fiche — un déclarant
  qui dépose une déclaration de TVA a forcément un numéro de TVA. Seul un
  `NE` explicite vaut renonciation. Le matricule `0000 0000 000` du gabarit
  est rejeté de même.
- Le n° de TVA et les alias alimentent la détection achat / vente (source
  « identité » du contrôle C11).

### Interfaces
- **Tkinter abandonné** : le Tcl/Tk 8.5 fourni par macOS produit des fenêtres
  noires ou vides. Tout passe par **osascript** (dialogues natifs), présent
  partout, rien à installer.
- **Lanceurs double-clic** : bundles `.app` avec stub bash. pCloud ne conserve
  pas le bit exécutable → l'app doit être copiée hors pCloud (Bureau), puis
  `chmod +x` et `codesign --force --deep --sign -`.
- Le stub complète le `PATH` avec Homebrew : lancé par le Finder, il n'a que
  `/usr/bin:/bin:/usr/sbin:/sbin` et ne trouve donc pas `pdftotext`, ce qui
  faisait classer **toutes** les factures en « scan ».
- L'accès aux fichiers pCloud depuis une app lancée par le Finder exige une
  autorisation « Accès complet au disque » accordée **au binaire python3**,
  pas au bundle : le script se remplace par python3 via `exec`.

### Page web locale
- Écoute sur **127.0.0.1 uniquement**, aucune ressource externe chargée.
- **Type de déclaration et période choisis dès l'écran 1** : le contrôle C8
  (facture manquante chez un fournisseur récurrent) dépend de la *largeur* de
  la période, pas seulement des dates présentes. Un aperçu annuel produisait
  des faux positifs massifs.
- Les avis non bloquants (C9) s'accrochent à la ligne existante au lieu de
  créer une ligne dupliquée ; seules les bloquantes ont leur propre ligne.
- La lecture des factures est déclenchée au passage écran 1 → écran 2.
- Les aperçus vont dans `apercu/`, jamais dans `annexes/`, tant que le prorata
  n'est pas confirmé à l'écran 3.

### Réplique PDF client
- Porte l'**identité du cabinet** (`config/cabinet.yaml`), **jamais** les
  armoiries ni l'en-tête de l'État. Mention obligatoire en tête.
- Les cases sans montant sont **affichées vides, pas omises**.
- Le total de section est la case que le formulaire désigne lui-même comme
  total — jamais une addition faite par la page.
- Filigrane **BROUILLON — NON DÉPOSÉ** tant que la période n'est pas marquée
  déposée dans `dossiers/<CODE>/depots.json`. Aucun paramètre ne permet de le
  retirer : seul le marquage de dépôt le lève.
- Le marquage de dépôt est un geste explicite et tracé (date + référence
  d'accusé éventuelle).

### XML eCDF
- Le livrable déposable est un **XML** ; le XSD n'est que le schéma de contrôle.
- Types vérifiés dans la documentation officielle v2.0 : **`TVA_DECM`**
  (mensuel), **`TVA_DECT`** (trimestriel, distinct), **`TVA_DECA`** (annuel).
- `model="1"` : étayé par l'exemple officiel du CTIE, **non confirmé** pour
  l'année de référence 2025.
- **XSD reconstruit** depuis la documentation publique — l'officiel n'est
  téléchargeable que dans l'espace développeurs authentifié. Il accepte les 8
  déclarations TVA de l'exemple officiel et rejette 14 fautes sur 14 testées.
  Un XML valide chez nous reste à confirmer par le portail.
- Trois contrôles avant qu'un fichier soit proposé : schéma, relecture des
  montants depuis le XML produit, bornes de `Period` propres au type. Échec =
  aucun fichier laissé sur le disque.
- **Nom de fichier imposé par eCDF** (référence + `.xml`). La traçabilité
  société / période / version passe par le journal `ecdf-envois.json`.
- Aucun dépôt automatique : le transfert reste manuel.

### Validation métier
- La validation d'un livrable, c'est **l'accord du client par courriel**, pas
  un contrôle technique. D'où le dépôt manuel, hors interface.

---

## Prochaines étapes

1. **Remplir `config/cabinet.yaml`** (nom, adresse, contact, logo éventuel) —
   encore en « A COMPLETER ».
2. **Remplir `config/ecdf.yaml`** : préfixe agent (site eCDF, menu Transfert de
   fichiers), identifiant d'interface (demande d'accès développeur au CTIE),
   identifiants du cabinet. Tant que ces valeurs sont les gabarits, le XML
   produit sert aux tests de structure, pas au dépôt.
3. **Récupérer le XSD officiel** depuis l'espace développeurs si l'accès est
   obtenu, et le substituer au schéma reconstruit.
4. ~~Brancher réplique PDF et XML dans l'écran 3~~ — **fait le 07/09/2026**.
   Deux boutons dans « Livrables dérivés », endpoints `/api/replique` et
   `/api/xml`, rafraîchissement par `/api/fichiers`, aperçu PDF et XML dans
   le cadre de droite. Reste à faire : clic sur un montant → lignes d'annexe
   qui le composent.
5. ~~Corriger la réplique PDF~~ — **fait le 07/09/2026** (prorata sur deux
   lignes, encadré du solde renforcé, numéro de case retiré du libellé de
   total).
6. **Trancher** : la génération doit-elle être refusée quand il reste des
   exceptions bloquantes ? Aujourd'hui elle passe, les lignes sont exclues et
   le fait est signalé avant et après.
7. **Mapping annuel** : 72 cases décrites au 07/09/2026 (contre 45 avant), la
   section III du formulaire officiel étant désormais reprise en entier —
   ventilation par nature de dépense (stock 080 / investissement 084 /
   exploitation 088 / autres 179), détail de la taxe non déductible (094,
   096) et régularisations (098 à 101).
   Ces cases sont **affichées vides** dans la réplique et **omises** du XML :
   le moteur connaît l'origine de la taxe (facturée / intracom / import /
   autoliquidation, déduite du régime) mais pas la nature de la dépense, qui
   est une qualification comptable absente des factures.
   Les remplir suppose une ventilation produite par `annexes_tva.py` à partir
   d'une qualification saisie ligne par ligne — donc un diff sur un script
   protégé, à valider séparément.
8. ~~Avant tout push : `config/ecdf.yaml` n'est pas couvert par `.gitignore`~~
   — **fait le 07/09/2026**. Le fichier est exclu, `config/ecdf.template.yaml`
   le remplace au dépôt.
9. **Installer le CLI Claude Code** : `curl -fsSL https://claude.ai/install.sh | bash`.
   Absent de ce Mac, donc la lecture des factures ne peut pas tourner.
10. **Supprimer `_TEMPLATE-ANNEXES-TVA-ANCIEN-20260907`** sur pCloud une fois
    la nouvelle organisation éprouvée.
