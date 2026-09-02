# Publier ce dépôt sur GitHub

Le dépôt est **initialisé et committé en local**. Il ne restait qu'à le pousser :
le jeton GitHub de la session Claude est invalide, la création du dépôt distant
doit donc partir de ta machine, où tes identifiants sont disponibles.

GitHub n'accepte pas d'espace dans un nom de dépôt : `workflow VAT` devient
**`workflow-VAT`**.

## Option A — avec le client GitHub (`gh`)

```bash
cd "~/Documents/Claude setup/Workflows/VAT/_TEMPLATE-ANNEXES-TVA"
gh auth login                      # une seule fois
gh repo create workflow-VAT --private --source=. --remote=origin --push
```

## Option B — sans `gh`

Crée le dépôt **privé** `workflow-VAT` sur github.com, puis :

```bash
cd "~/Documents/Claude setup/Workflows/VAT/_TEMPLATE-ANNEXES-TVA"
git remote add origin https://github.com/<ton-compte>/workflow-VAT.git
git branch -M main
git push -u origin main
```

## Confidentialité — à vérifier avant de pousser

Le `.gitignore` exclut **toutes les données clients** :

- `dossiers/` — dossiers de travail réels, dont GACELS
- `*.pdf` sauf ceux des jeux d'essai fictifs
- `**/extraction/`, les classeurs produits et les instantanés `-DECLARE.json`

Le commit initial contient 32 fichiers : code, configurations, jeux d'essai
fictifs et leurs rapports. **Aucune donnée GACE, aucune facture réelle.**
Vérifie avant de pousser :

```bash
git ls-files | grep -iE "gace|gacels"      # doit ne rien renvoyer
```

**Dépôt privé recommandé** : le mapping des cases eCDF et les règles de
contrôle décrivent une méthode de travail interne.
