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

## Prompt 2 — La page web locale

À faire **après** avoir utilisé le workflow deux ou trois fois en conversation. Tu sauras alors ce qui te coûte du temps.

```
CONTEXTE
Même dossier, mêmes scripts, mêmes interdits qu'avant. Ne réécris pas
src/annexes_tva.py ni src/reconciliation.py : la page les appelle,
elle ne refait aucun calcul.

OBJECTIF
Une page qui s'ouvre dans mon navigateur et qui tourne uniquement sur
ma machine. Rien n'est publié, rien ne sort de mon Mac. Je dois pouvoir
la fermer et tout s'arrête.

Trois écrans, dans cet ordre.

ÉCRAN 1 — IMPORTATION
Je choisis la société et le dossier source des factures.
La page m'affiche l'inventaire : combien de documents uniques, combien
de doublons écartés et lesquels, combien de PDF natifs et combien de
scans. Un tableau liste chaque document avec son nom de fichier et le
sens détecté depuis le chemin.

ÉCRAN 2 — DÉTECTION
Un tableau, une ligne par facture, avec : date, tiers, numéro de
facture, sens achat ou vente, régime, base, taux, TVA.
Chaque ligne indique d'où vient le sens : chemin, identité des parties
ou extraction.
Les lignes en exception sont visuellement distinctes, avec le code du
contrôle et le message.
Quand je clique sur une ligne, le PDF de la facture s'ouvre à côté du
tableau, pour que je vérifie sans quitter la page.
Je peux corriger une valeur directement dans le tableau ; la correction
est écrite dans le JSON d'extraction, pas ailleurs.

ÉCRAN 3 — COMPILATION
Je choisis le type de déclaration et la période.
La page m'affiche le prorata calculé sur le chiffre d'affaires de
l'année, avec le détail du calcul, et me laisse le remplacer.
Un bouton lance la génération. Quand c'est fini, la page affiche les
totaux et me donne un lien vers chacun des quatre fichiers produits.

CONTRAINTES
- Local uniquement, jamais accessible depuis l'extérieur de ma machine
- Aucun calcul dans la page : tout passe par les scripts existants
- Aucune écriture dans le dossier client
- Si un contrôle bloquant échoue, la génération est refusée et la page
  me dit pourquoi

MÉTHODE
Construis l'écran 1 seul. Montre-le-moi. On ne passe à l'écran 2 que
quand le 1 me convient. Explique-moi comment je lance et comment
j'arrête la page.
```

**Le passage qui compte.** « Construis l'écran 1 seul. » Sans cette phrase, tu reçois trois écrans à moitié faits que personne ne peut tester. Avec elle, tu valides à chaque étape et tu gardes la main.

**Le passage qui protège.** « Local uniquement, jamais accessible depuis l'extérieur de ma machine. » Une page web mal configurée est visible depuis le réseau. Avec des factures clients à l'écran, c'est un incident de confidentialité. Cette phrase doit figurer dans le prompt, pas dans ta mémoire.

---

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
