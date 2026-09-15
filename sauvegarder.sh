#!/bin/bash
# =====================================================================
# Sauvegarde du depot de travail vers pCloud -- SENS UNIQUE.
#
# La copie de travail vit sur le disque local (~/CampagneTVA) : pCloud
# est un volume FUSE qui ne conserve pas le bit executable, impose une
# autorisation "Acces complet au disque" au binaire python3, melange les
# formes NFC/NFD des accents et peut se demonter en pleine session.
# Aucun de ces defauts ne gene une SAUVEGARDE ; tous rendent une copie
# de TRAVAIL fragile. D'ou la separation.
#
# Ce script ne lit jamais depuis pCloud vers le local : la destination
# est un miroir, jamais une source. Le code, lui, est deja versionne
# dans git ; pCloud couvre ce que .gitignore exclut, a commencer par
# dossiers/ (donnees clients).
#
# Lancement : ./sauvegarder.sh          (sauvegarde reelle)
#             ./sauvegarder.sh --essai  (montre ce qui serait fait)
# =====================================================================
set -u

SOURCE="$HOME/CampagneTVA"
# Le nom dit ce que c'est. L'ancienne copie de travail vit a cote sous
# _TEMPLATE-ANNEXES-TVA-ANCIEN-20260907, gelee, a supprimer quand la
# nouvelle organisation aura fait ses preuves.
CIBLE="$HOME/pCloud Drive/pCloud Backup/MacBookAirdeThomas/Documents/Claude setup/Workflows/VAT/CampagneTVA-SAUVEGARDE"
JOURNAL="$HOME/campagne_tva_sauvegarde.log"

ESSAI=""
[ "${1:-}" = "--essai" ] && ESSAI="--dry-run"

# Le journal seul ne suffit pas : du 8 au 14 septembre la sauvegarde a ete
# REPORTEE chaque soir sans que personne ne le lise. Toute issue autre que
# OK produit donc une notification macOS (l'en-tete de la page le signale
# aussi). Pas de notification en mode essai.
notifier() {
    [ -n "$ESSAI" ] && return 0
    osascript -e "display notification \"$1\" with title \"Campagne TVA — sauvegarde\" sound name \"Basso\"" >/dev/null 2>&1 || true
}

{
echo "=== $(date '+%d/%m/%Y %H:%M:%S') ${ESSAI:+[ESSAI]} ==="

if [ ! -d "$SOURCE" ]; then
    echo "ECHEC : source introuvable -- $SOURCE"
    exit 1
fi

# pCloud monte son volume a la demande. Sauvegarder alors que le volume
# est absent creerait un dossier ordinaire dans le point de montage vide,
# invisible ensuite une fois pCloud remonte par-dessus. On verifie donc
# que c'est bien un volume monte, pas un simple dossier.
if ! mount | grep -q "on $HOME/pCloud Drive "; then
    echo "REPORTE : pCloud n'est pas monte -- rien n'a ete copie."
    notifier "pCloud n'est pas monté — sauvegarde non faite."
    exit 0
fi

# Figurer dans la table des montages ne suffit pas : le montage FUSE de
# pCloud peut y rester alors que le volume ne repond plus ("Device not
# configured"). Seule une lecture reelle le prouve. Sans ce test, rsync
# part et echoue a mi-parcours, laissant une sauvegarde tronquee.
if ! ls "$HOME/pCloud Drive" >/dev/null 2>&1; then
    echo "REPORTE : pCloud est monte mais ne repond pas -- rien n'a ete copie."
    notifier "pCloud ne répond pas — sauvegarde non faite. Relancer pCloud."
    exit 0
fi

mkdir -p "$CIBLE" 2>/dev/null

# --delete : miroir strict, les suppressions locales sont repercutees.
#   Le filet de securite est le versionnage de pCloud, qui permet de
#   restaurer un fichier efface.
# --no-perms / --no-group / --no-owner : pcloudfs ne sait pas porter ces
#   attributs ; sans ces options rsync signale une erreur a chaque fichier.
# --exclude .git : l'historique est deja sur GitHub, et des milliers de
#   petits objets sur un volume reseau rendent la sauvegarde interminable.
rsync -rlt $ESSAI --delete \
      --no-perms --no-group --no-owner \
      --exclude '.git/' \
      --exclude '__pycache__/' \
      --exclude '.DS_Store' \
      --exclude '*.pyc' \
      --exclude '_LIRE-MOI-SAUVEGARDE.txt' \
      --itemize-changes \
      "$SOURCE/" "$CIBLE/"
CODE=$?

if [ $CODE -eq 0 ]; then
    echo "OK -- $(cd "$SOURCE" && find . -type f ! -path './.git/*' | wc -l | tr -d ' ') fichiers en source"
else
    echo "ECHEC rsync (code $CODE)"
    notifier "Échec de la copie (rsync code $CODE) — voir $JOURNAL"
fi
exit $CODE
} 2>&1 | tee -a "$JOURNAL"
