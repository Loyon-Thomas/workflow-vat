# Publier ce dépôt sur GitHub

Le dépôt est **initialisé et committé en local**, 4 commits, 65 fichiers.

Il n'a pas pu être poussé depuis la session Claude : le jeton GitHub de la
session est limité aux dépôts qu'elle a déjà configurés, et refuse aussi bien la
création (`POST /user/repos` → 403) que l'accès à un dépôt tiers. Le compte
identifié est **`Loyon-Thomas`**.

GitHub n'accepte pas d'espace dans un nom de dépôt : `workflow VAT` devient
**`workflow-VAT`**.

## Option A — client GitHub

```bash
brew install gh                    # gh n'est pas installé sur cette machine
cd "~/Documents/Claude setup/Workflows/VAT/_TEMPLATE-ANNEXES-TVA"
gh auth login
gh repo create workflow-VAT --private --source=. --remote=origin --push
```

## Option B — sans client

Créer le dépôt **privé** `workflow-VAT` sur github.com (sans README ni
.gitignore initial), puis :

```bash
cd "~/Documents/Claude setup/Workflows/VAT/_TEMPLATE-ANNEXES-TVA"
git remote add origin https://github.com/Loyon-Thomas/workflow-VAT.git
git branch -M main
git push -u origin main
```

Git demandera l'identifiant et un **jeton d'accès personnel** (Settings →
Developer settings → Personal access tokens), pas le mot de passe du compte.

## Confidentialité — vérifié

Le `.gitignore` exclut `dossiers/` (dont GACELS), tous les PDF hors jeux
d'essai fictifs, les dossiers `extraction/`, les classeurs produits et les
instantanés `-DECLARE.json`.

```bash
git ls-files | grep -i gace       # ne renvoie rien
```

**Dépôt privé** : le mapping des cases eCDF et les règles de contrôle décrivent
une méthode de travail interne.
