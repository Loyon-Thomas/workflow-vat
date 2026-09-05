# Comment demander à Claude Code de construire les interfaces

Prompts prêts à copier, pour un usage sans expérience de développement.

---

## La méthode, en quatre règles

**1. Décris le résultat, pas la technique.** Tu n'as pas à choisir un langage ni une bibliothèque. « Je veux voir la liste des factures avec leur montant » suffit. « Utilise React » serait une contrainte que tu n'es pas en position d'arbitrer.

**2. Donne le contexte et les interdits.** Ce qui existe déjà, ce qu'il ne doit pas toucher. Sans ça, il réécrit ce qui marche déjà.

**3. Une chose à la fois, testée avant la suivante.** Un prompt qui demande dix fonctions produit dix approximations. Un prompt qui en demande une produit une chose qui marche.

**4. Exige qu'il explique et qu'il teste.** Deux phrases à ajouter à chaque demande :

> Explique-moi en français simple ce que fait le fichier que tu viens d'écrire.
> Lance-le sur le jeu d'essai tests/jeu-2025 et montre-moi le résultat avant de me dire que c'est fini.

---

## Ce qu'il faut savoir avant de demander

La chaîne comporte trois moments. Le deuxième est le seul qui ait besoin d'intelligence.

| Étape | Ce qui se passe | Automatisable seul ? |
|---|---|---|
| Importation | Parcours du dossier, dédoublonnage, tri natif/scan | Oui, déjà fait par le script |
| **Lecture et détection** | **Lire chaque facture, en extraire tiers, numéro, date, montants, taux, régime** | **Non — c'est là que Claude intervient** |
| Compilation | Contrôles, agrégation, annexes, correspondance eCDF | Oui, déjà fait par le script |

Conséquence pratique : un fichier double-cliquable ne peut pas *lire* les factures tout seul. Il doit soit appeler Claude Code en arrière-plan, soit s'arrêter et te demander de lancer la lecture. Ton prompt doit poser la question plutôt que de présumer la réponse.

---

## Prompt 1 — Le fichier double-cliquable

À coller tel quel dans Claude Code, dans le dossier `_TEMPLATE-ANNEXES-TVA`.

```
CONTEXTE
Ce dossier contient deux scripts Python qui fonctionnent et sont testés :
src/annexes_tva.py et src/reconciliation.py. Ne les modifie pas.
Lis d'abord README.md et SPECIFICATION.md pour comprendre la chaîne.

OBJECTIF
Je veux un fichier que je pose sur mon Bureau et que je double-clique
pour lancer une campagne TVA complète, sans ouvrir de terminal.

Quand je double-clique, une fenêtre s'ouvre et me demande, dans l'ordre :
1. quelle société traiter, en me proposant la liste de celles qui
   existent dans dossiers/
2. quel type de déclaration : mensuelle, trimestrielle ou annuelle
3. l'année, puis le mois ou le trimestre

Ensuite il enchaîne tout seul :
- l'inventaire des factures du dossier client, en m'affichant combien de
  documents uniques, combien de doublons écartés, combien de scans
- la lecture des factures et l'écriture des JSON d'extraction
- la génération des annexes, du rapport d'exceptions et de la
  correspondance eCDF

À la fin, il m'affiche un résumé lisible : nombre de lignes retenues,
nombre d'exceptions bloquantes, TVA en amont et déductible, puis il
ouvre le dossier des annexes dans le Finder.

POINT À TRANCHER
La lecture des factures a besoin de toi, pas seulement d'un script.
Dis-moi honnêtement comment tu comptes t'y prendre, ce que ça implique
en coût et en fiabilité, et propose-moi une solution de repli si la
lecture automatique échoue sur un document. Ne construis rien avant que
j'aie validé ce point.

CONTRAINTES
- N'écris jamais dans les dossiers clients, seulement en lecture
- Aucun dépôt automatique à l'administration
- Si une exception bloquante apparaît, arrête-toi et affiche-la
- Le fichier doit fonctionner même si mon Mac n'a rien d'installé
  d'autre que Python
```

**Pourquoi ce prompt fonctionne.** Il donne le contexte et l'interdit avant l'objectif. Il décrit un comportement observable — ce que je vois quand je double-clique — sans imposer de technique. Il isole le seul point réellement incertain et interdit de construire avant tranchage. Il rappelle les garde-fous métier.

**Ce que tu fais ensuite.** Il répond sur le point à trancher. Tu choisis. Puis :

```
Va-y, construis-le. Ensuite explique-moi en français simple comment il
marche, et fais-moi une démonstration sur tests/jeu-2025 pour que je
voie le résultat avant de l'utiliser sur un vrai dossier.
```

---

## Prompt 2 — La page web locale, avec historique des productions

La page ne se contente pas de produire : elle **conserve et rend consultable**
tout ce qui a été produit. Les instantanés `-DECLARE.json` écrits à chaque
exécution en sont déjà la matière ; la page les indexe et les présente.

### Étape préalable — enrichir le modèle de données

À faire avant de construire quoi que ce soit d'affichage.

```
CONTEXTE
Chaque exécution de src/annexes_tva.py écrit un instantané -DECLARE.json à
côté des annexes. Ces instantanés doivent devenir un historique consultable
des productions, pas seulement un sous-produit.

Aujourd'hui deux générations d'une même période coexistent sans que rien
n'indique laquelle fait foi, et des périodes produites pour tester se
mélangent aux périodes réelles.

OBJECTIF
Ajoute trois informations à l'instantané, sans casser reconciliation.py qui
le lit déjà :

1. VERSION
   - un marqueur indiquant si cette génération est la version courante de
     la période
   - la date de génération, déjà présente
   - le nom du fichier qu'elle remplace, s'il y en a un
   Quand une période est régénérée, l'ancienne version est conservée et
   perd son marqueur de version courante. Rien n'est écrasé.

2. DÉPÔT
   - un statut : brouillon, validé par le client, déposé
   - la date de dépôt quand le statut est "déposé"
   - un champ libre optionnel pour une référence d'accusé
   - la liste des livrables produits pour cette période, avec leur nom de
     fichier, pour qu'on puisse les rouvrir depuis l'historique

3. NATURE
   - réelle ou essai, pour que les périodes produites pour tester
     n'apparaissent pas dans l'historique de production

Écris aussi une commande qui parcourt les instantanés existants et leur
ajoute ces champs avec des valeurs par défaut raisonnables, en me montrant
ce qu'elle compte faire avant de le faire.

CONTRAINTES
- Les fichiers restent la source de vérité. Si tu proposes une base de
  données, elle ne peut être qu'un index reconstructible depuis les
  fichiers, jamais l'original.
- reconciliation.py doit continuer à fonctionner sans modification
- Lance les deux jeux d'essai après ta modification et montre-moi que les
  résultats sont inchangés
```

### Écran 1 — Accueil portefeuille

```
Une page qui s'ouvre dans mon navigateur et tourne uniquement sur ma
machine. Rien n'est publié, rien ne sort de mon Mac. Je la ferme, tout
s'arrête.

ÉCRAN D'ACCUEIL
La liste de toutes les sociétés du dossier dossiers/.
Pour chacune : sa périodicité, et l'état de ses périodes de l'exercice en
cours sous forme de ligne de temps — janvier, février, mars et ainsi de
suite pour une mensuelle, T1 à T4 pour une trimestrielle.

Chaque période porte visuellement son état : non produite, brouillon,
validée client, déposée le tant. Je dois voir d'un coup d'œil ce qui reste
à traiter sur l'ensemble du portefeuille.

Un clic sur une société ouvre son historique. Un clic sur une période
ouvre cette période.

Construis cet écran seul. Montre-le-moi. Explique-moi comment je lance la
page et comment je l'arrête.
```

### Écran 2 — Historique d'une société

```
La liste des périodes déclarées, de la plus récente à la plus ancienne.
Pour chacune : période, statut de dépôt et date, date de génération,
nombre de lignes, nombre d'exceptions, TVA en amont, TVA déductible,
prorata appliqué et son origine.

Quand une période a plusieurs versions, elles sont regroupées : la version
courante est mise en avant, les précédentes accessibles en dépliant.
Je peux comparer deux versions et voir ce qui a changé entre elles.

Depuis chaque ligne, je peux ouvrir les quatre livrables produits :
le classeur, le rapport d'exceptions, la correspondance eCDF, l'instantané.

Je peux renseigner le statut de dépôt et sa date directement ici.

Une recherche transversale : taper un nom de fournisseur ou un numéro de
facture me montre toutes les périodes où il apparaît, avec les montants.
```

### Écran 3 — Traiter une période

```
Trois moments, dans l'ordre.

IMPORTATION
Je choisis le dossier source des factures.
La page affiche l'inventaire : documents uniques, doublons écartés et
lesquels, PDF natifs et scans. Un tableau liste chaque document avec son
nom de fichier et le sens détecté depuis le chemin.

DÉTECTION
Un tableau, une ligne par facture : date, tiers, numéro, sens, régime,
base, taux, TVA. Chaque ligne indique d'où vient le sens — chemin,
identité des parties ou extraction.
Les lignes en exception sont distinctes, avec le code du contrôle et le
message.
Un clic sur une ligne ouvre le PDF de la facture à côté du tableau.

Je peux corriger une valeur directement dans le tableau. Toute correction :
- est écrite dans le JSON d'extraction, jamais ailleurs
- conserve la valeur d'origine et la date de correction dans ce même JSON
- déclenche une nouvelle passe de contrôles sur la ligne corrigée
Je dois pouvoir voir, plus tard, quelles valeurs ont été corrigées à la
main et lesquelles viennent de la lecture du document.

COMPILATION
Je choisis le type de déclaration et la période.
La page affiche le prorata calculé sur le chiffre d'affaires de l'année,
avec le détail du calcul, et me laisse le remplacer.
Un bouton lance la génération. À la fin, les totaux s'affichent et la
nouvelle version devient la version courante de la période.

CONTRAINTES
- Local uniquement, jamais accessible depuis l'extérieur de ma machine
- Aucun calcul dans la page : tout passe par les scripts existants
- Aucune écriture dans le dossier client
- Si un contrôle bloquant échoue, la génération est refusée avec le motif

MÉTHODE
Un écran à la fois. On ne passe au suivant que quand le précédent me
convient.
```

## Les phrases à réutiliser partout

À garder sous la main. Elles s'ajoutent à n'importe quelle demande.

| Situation | Phrase |
|---|---|
| Il part trop vite | `Ne construis rien. Explique-moi d'abord ce que tu comptes faire et pourquoi.` |
| Tu ne comprends pas ce qu'il a fait | `Explique-moi ce fichier ligne par ligne, en français simple, comme à quelqu'un qui n'a jamais programmé.` |
| Il dit que c'est fini | `Prouve-le. Lance-le sur tests/jeu-2025 et montre-moi la sortie.` |
| Une modification t'inquiète | `Montre-moi le diff avant de committer.` |
| Ça a cassé quelque chose | `Annule tes modifications depuis le dernier commit et reprends autrement.` |
| Tu veux garder l'état | `Committe maintenant avec un message qui explique ce qui a changé.` |

---

## L'ordre à respecter

1. Utiliser le workflow en conversation, deux ou trois campagnes réelles
2. Prompt 1, le fichier double-cliquable — si tu répètes toujours la même séquence
3. Prompt 2, la page web — seulement si la revue des exceptions est ce qui te coûte le plus

Sauter l'étape 1 est la seule vraie erreur possible ici : tu ferais construire des écrans avant de savoir lesquels te servent.
