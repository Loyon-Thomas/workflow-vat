# Guide de démarrage — Claude Code pour le workflow TVA

Destiné à un usage sans expérience de développement.

---

## Ce qu'est Claude Code

Un assistant qui travaille **directement dans un dossier de ton disque**. Tu lui écris ce que tu veux en français, il lit les fichiers, lance les scripts, écrit les résultats. La différence avec la conversation actuelle : il tourne sur ta machine, en accès direct, sans passer par un pont.

Ce n'est **pas** un logiciel avec des boutons. C'est une conversation qui a la main sur un dossier.

---

## Prérequis

| Élément | Requis |
|---|---|
| macOS | 13.0 ou plus récent |
| Mémoire | 4 Go minimum |
| Abonnement | Pro, Max, Team ou Enterprise — le plan gratuit ne donne pas accès |
| Connexion | Internet |

---

## Étape 1 — Installer

### Option A — sans terminal (recommandée)

L'application Claude pour Mac permet d'utiliser Claude Code sans ligne de commande. Si tu l'as déjà — c'est le cas, cette conversation y tourne — vérifie dans le menu latéral la présence d'une entrée **Code**. Si elle y est, passe à l'étape 2.

Sinon, télécharge la dernière version depuis claude.ai et relance l'application.

### Option B — par le terminal

Ouvre Terminal (Cmd + Espace, tape `Terminal`), colle :

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Vérifie que c'est en place :

```bash
claude --version
```

Une version s'affiche, du type `2.1.211 (Claude Code)`. En cas de doute :

```bash
claude doctor
```

Cette commande liste l'état de l'installation sans rien lancer.

---

## Étape 2 — Ouvrir le dossier de travail

Claude Code travaille **dans un dossier à la fois**. Pour le workflow TVA, c'est celui-ci :

```
~/Documents/Claude setup/Workflows/VAT/_TEMPLATE-ANNEXES-TVA
```

**Depuis l'application :** ouvre l'entrée Code, choisis ce dossier comme projet.

**Depuis le terminal :**

```bash
cd "$HOME/Documents/Claude setup/Workflows/VAT/_TEMPLATE-ANNEXES-TVA"
claude
```

Une session s'ouvre. Tu écris, il répond.

À la première connexion, une page s'ouvre dans le navigateur pour t'identifier. Une seule fois.

---

## Étape 3 — Poser les règles une fois pour toutes

Claude Code lit un fichier nommé `CLAUDE.md` à la racine du dossier au démarrage de chaque session. Ce qui y est écrit s'applique sans que tu aies à le répéter.

Écris-lui, en première session :

> Crée un fichier CLAUDE.md à la racine qui rappelle : les scripts src/annexes_tva.py et src/reconciliation.py ne doivent jamais être modifiés sans que je valide le diff ; aucun calcul ne doit être refait à la main hors de ces scripts ; rien ne doit être écrit dans les dossiers clients ; toute exception bloquante doit m'être remontée, jamais contournée.

Il le crée. Tu le relis. C'est ton garde-fou permanent.

---

## Étape 4 — Premier usage réel

Une fois le dossier client connecté, une demande type :

> Traite le dossier client GACELS pour le troisième trimestre 2025. Fais l'inventaire, lis chaque facture, écris les JSON d'extraction, puis lance la génération des annexes. Montre-moi le rapport d'exceptions avant que je valide.

Il enchaîne les étapes et s'arrête sur les exceptions. Tu tranches, il reprend.

Autres demandes utiles :

> Réconcilie l'exercice 2025 de GACELS avec l'export comptable que je viens de déposer dans le dossier.

> Ajoute Post Telecom au référentiel de GACELS : fournisseur mensuel, libellé standard "Phone costs", taux 17 %.

> Le rapport d'exceptions signale une divergence de sens sur la facture X. Explique-moi pourquoi et propose une correction.

---

## Règles de sécurité

**Il demande avant d'agir.** À chaque commande qui modifie quelque chose, il affiche ce qu'il va faire et attend ton accord. Ne coche pas « toujours autoriser » sur des commandes de suppression.

**Un dossier à la fois.** Il ne voit que le dossier ouvert. Il n'accède ni à tes mails, ni au reste du disque.

**Trois interdits à maintenir :**

1. Aucune écriture dans les dossiers clients — seulement lecture
2. Aucun dépôt automatique à l'administration
3. Aucune modification des scripts sans que tu relises le changement

**Git est ton filet.** Le dossier est un dépôt : si une modification te déplaît, `git diff` montre ce qui a changé et `git checkout .` annule tout depuis le dernier commit. Demande-lui de committer après chaque étape validée.

---

## Ce que Claude Code peut produire comme interface

Claude Code n'est pas lui-même une application. Il **écrit** le programme que tu lui demandes. Quatre formes possibles, par effort croissant.

### 1. Rien de plus — piloter en conversation

Tu écris ta demande, il exécute. **Zéro développement, disponible immédiatement.**

Limite : il faut ouvrir une session et formuler la demande à chaque fois.

### 2. Un fichier double-cliquable

Un fichier `.command` posé sur le Bureau. Double-clic, une fenêtre s'ouvre, le script pose ses questions — société, période, prorata — et produit les fichiers.

Une demi-journée de mise au point. Aucune dépendance nouvelle. Utile si tu répètes toujours la même séquence.

### 3. Une page web locale — *le meilleur rapport valeur / effort*

Une page qui s'ouvre dans ton navigateur, mais qui tourne **sur ta machine uniquement**. Rien n'est publié, rien ne sort. Tu la fermes, elle s'arrête.

Ce qu'elle apporte concrètement :

- Choisir la société et la période dans des listes déroulantes
- Un bouton pour lancer le traitement, une barre de progression
- **Le tableau des exceptions à l'écran, cliquable** : chaque ligne ouvre la facture concernée à côté du montant contesté

C'est ce dernier point qui compte. Aujourd'hui tu lis un fichier texte et tu retournes chercher la facture à la main. Une page web supprime cet aller-retour, qui est l'essentiel du temps passé sur les exceptions.

Compter deux à trois jours de mise au point avec Claude Code.

### 4. Une application Mac packagée

Une icône dans le Dock, comme un logiciel du commerce.

**Peu recommandé.** Il faut la signer chez Apple, gérer les mises à jour, la réinstaller à chaque changement. Pour un usage personnel ou une petite équipe, la page web locale offre le même confort sans aucune de ces contraintes.

### Recommandation

**Commence par le niveau 1** — piloter en conversation, sans rien construire. Après deux ou trois campagnes réelles, tu sauras ce qui te coûte du temps. C'est à ce moment-là qu'on décide s'il faut la page web, et surtout **quoi mettre dedans**.

Construire l'interface avant d'avoir cette réponse, c'est fabriquer des boutons qu'on n'utilisera pas.

### Un avertissement qui ne se négocie pas

Si une page web est construite un jour, elle doit rester **locale**. Publier une interface contenant des factures clients sur un serveur, même privé, change complètement la nature du risque : confidentialité, RGPD, secret professionnel. Aucun gain de confort ne le justifie.
