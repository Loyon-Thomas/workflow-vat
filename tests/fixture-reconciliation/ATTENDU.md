# Jeu de test — réconciliation

Douze factures en comptabilité (`gl.csv`), deux périodes déclarées (Q1, Q2),
défauts injectés volontairement. Vérité terrain :

| Défaut injecté | Détection attendue |
|---|---|
| F2405, F2406, F2411 comptabilisées, jamais déclarées | **Comptabilisé non déclaré ×3** — TVA 246,50 |
| F9999 déclarée sans écriture comptable | **Déclaré non comptabilisé ×1** |
| F2404 déclarée 410,00 / 69,70 au lieu de 400,00 / 68,00 | **Écart** +10,00 / +1,70 |
| F2408 déclarée TVA 140,00 au lieu de 136,00 | **Écart** +4,00 |
| F2401 déclarée en Q1 **et** en Q2 | **R3** + **écart** +100,00 / +17,00 |
| Q1 générée avec 1 exception bloquante non soldée | **R2** |
| Q3 et Q4 jamais déclarées | **R1 ×2** |

Résultat obtenu : `concordants 6 | ecarts 3 | declare non compta 1 |
COMPTA NON DECLARE 3 | anomalies 4`. Conforme.

**Le doublon est attrapé deux fois** : par R3 au cumul, et par un écart dont le
montant est exactement celui de la facture dupliquée. Les deux signaux se
corroborent.

Rejouer :

```bash
python3 src/reconciliation.py --dossier tests/fixture-reconciliation \
        --annee 2024 --gl tests/fixture-reconciliation/gl.csv
```
