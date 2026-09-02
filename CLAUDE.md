# Règles du dépôt

Règles à respecter dans toute session Claude Code sur ce dépôt.

1. **Scripts protégés** — `src/annexes_tva.py` et `src/reconciliation.py` ne doivent jamais être modifiés sans que Thomas valide le diff au préalable.
2. **Aucun recalcul hors scripts** — aucun calcul TVA ne doit être refait à la main ou recalculé en dehors de ces deux scripts. Ils sont la seule source de vérité pour les montants.
3. **Dossiers clients intouchés** — rien ne doit être écrit directement dans `dossiers/<client>/` en dehors de ce que produisent ces scripts. Pas d'édition manuelle des fichiers clients.
4. **Exceptions bloquantes toujours remontées** — toute exception bloquante rencontrée pendant un traitement doit être signalée à Thomas, jamais contournée silencieusement (pas de valeur par défaut, pas de skip discret).
