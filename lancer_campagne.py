#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lanceur d'une campagne TVA complete, pour n'importe quelle societe presente
sous dossiers/ : inventaire, lecture des factures (Claude Code, mode non
interactif), generation des annexes.

Pilote src/annexes_tva.py par sa ligne de commande, sans jamais le modifier.
Aucun calcul TVA n'est refait ici : ce fichier orchestre, lit et affiche.

Interface : boites de dialogue natives macOS (osascript/AppleScript), pas de
bibliotheque graphique tierce -- Tkinter s'est avere trop peu fiable sur le
Tcl/Tk deprecie que macOS fournit par defaut (fenetres noires ou vides selon
le mode sombre). osascript est present sur tout Mac, sans rien a installer.
"""
import datetime as dt
import glob
import importlib.util
import json
import os
import pty
import re
import select
import shutil
import subprocess
import tempfile
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
DOSSIERS = os.path.join(REPO, "dossiers")
SCRIPT_ANNEXES = os.path.join(REPO, "src", "annexes_tva.py")
CONFIG_LOCAL = os.path.expanduser("~/.campagne_tva_lanceur.json")

# Flag de lecture seule pour l'invocation de Claude Code en mode non
# interactif : a verifier sur la machine cible (`claude -p --help`).
FLAGS_LECTURE_SEULE = ["--allowedTools", "Read"]

REGIMES_ADMIS = ["achat_lu", "exonere_art44", "acq_intracom", "service_autoliq",
                  "import", "vente_lu", "livraison_intracom", "export", "hors_ue"]

CHAMPS_REQUIS = ["sens", "emetteur", "destinataire", "tiers", "num_facture",
                  "date", "devise", "taux_change", "total_ttc", "regime",
                  "source_extraction", "lignes"]

PROMPT_ATTENTE = re.compile(r"[:\]]\s*$")

yaml = None


def load_yaml():
    global yaml
    if yaml is None:
        import yaml as _y
        yaml = _y
    return yaml


def module_present(nom):
    return importlib.util.find_spec(nom) is not None


# ---------------------------------------------------------------------
# Boites de dialogue natives macOS (osascript)
# ---------------------------------------------------------------------

def _esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def osa(script, timeout=600):
    try:
        r = subprocess.run(["osascript", "-e", script],
                            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\n")


def choisir_dans_liste(items, invite, titre="Campagne TVA"):
    liste_as = ", ".join('"%s"' % _esc(x) for x in items)
    script = (
        'set r to choose from list {%s} with title "%s" with prompt "%s"\n'
        'if r is false then\n'
        '    "ANNULE"\n'
        'else\n'
        '    item 1 of r\n'
        'end if' % (liste_as, _esc(titre), _esc(invite)))
    res = osa(script)
    if res is None or res in ("ANNULE", ""):
        return None
    return res


def demander_texte(invite, defaut, titre="Campagne TVA"):
    script = (
        'try\n'
        '    set r to display dialog "%s" default answer "%s" with title "%s"\n'
        '    text returned of r\n'
        'on error number -128\n'
        '    "ANNULE"\n'
        'end try' % (_esc(invite), _esc(defaut), _esc(titre)))
    res = osa(script)
    if res is None or res == "ANNULE":
        return None
    return res


def confirmer(message, titre="Campagne TVA"):
    script = (
        'try\n'
        '    display dialog "%s" with title "%s" '
        'buttons {"Annuler", "Installer"} default button "Installer"\n'
        '    "OUI"\n'
        'on error number -128\n'
        '    "NON"\n'
        'end try' % (_esc(message), _esc(titre)))
    return osa(script) == "OUI"


def choisir_fichier(invite):
    script = (
        'try\n'
        '    set f to choose file with prompt "%s"\n'
        '    POSIX path of f\n'
        'on error number -128\n'
        '    "ANNULE"\n'
        'end try' % _esc(invite))
    res = osa(script)
    if res is None or res == "ANNULE":
        return None
    return res


def notifier(message, titre="Campagne TVA"):
    script = 'display notification "%s" with title "%s"' % (_esc(message), _esc(titre))
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def alerte(message, titre="Campagne TVA", critique=False):
    extra = " as critical" if critique else ""
    script = 'display alert "%s" message "%s"%s' % (_esc(titre), _esc(message), extra)
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
    except Exception:
        print(titre, "-", message)


# ---------------------------------------------------------------------
# Dependances (pyyaml, openpyxl) deja requises par les scripts proteges
# ---------------------------------------------------------------------

def ensure_dependencies():
    manquants = [m for m in ("yaml", "openpyxl") if not module_present(m)]
    if not manquants:
        return True
    # Ecrit AVANT toute tentative de dialogue : lance sans terminal ni
    # interface (double-clic, tache de fond), un echec ici ne laissait
    # aucune trace et le serveur s'arretait sans un mot. Le journal doit
    # toujours dire pourquoi.
    print("Modules absents de %s : %s" % (sys.executable, ", ".join(manquants)),
          file=sys.stderr)
    paquets = {"yaml": "pyyaml", "openpyxl": "openpyxl"}
    liste = ", ".join(paquets[m] for m in manquants)
    if not confirmer(
            "Les scripts de ce dossier necessitent %s, absent(s) de ce Python. "
            "Installer maintenant (pip3, en arriere-plan) ?" % liste):
        alerte("Sans ces paquets, la campagne ne peut pas etre generee.\n"
               "Installation manuelle : pip3 install %s" % liste, critique=True)
        return False
    notifier("Installation de %s..." % liste)
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet"] +
                        [paquets[m] for m in manquants],
                        check=True, capture_output=True, text=True, timeout=180)
    except subprocess.CalledProcessError as e:
        alerte("Echec de l'installation : %s" % (e.stderr or str(e)), critique=True)
        return False
    except subprocess.TimeoutExpired:
        alerte("Echec de l'installation : delai depasse.", critique=True)
        return False
    importlib.invalidate_caches()
    encore_manquants = [m for m in manquants if not module_present(m)]
    if encore_manquants:
        alerte("Installation incomplete, toujours absent : %s"
               % ", ".join(encore_manquants), critique=True)
        return False
    return True


# Emplacements ou l'executable claude atterrit selon le mode d'installation.
# Lance par le Finder, le PATH se reduit a /usr/bin:/bin:/usr/sbin:/sbin :
# shutil.which() seul echoue alors meme quand la commande existe. On elargit
# donc la recherche a ces emplacements connus avant d'abandonner.
EMPLACEMENTS_CLAUDE = [
    "~/.claude/local/claude",       # installateur natif (claude.ai/install.sh)
    "~/.local/bin/claude",          # installateur natif, variante XDG
    "/opt/homebrew/bin/claude",     # Homebrew, Apple Silicon
    "/usr/local/bin/claude",        # Homebrew Intel, ou npm -g par defaut
    "~/.bun/bin/claude",
    "~/.volta/bin/claude",
    "~/.yarn/bin/claude",
    "~/node_modules/.bin/claude",
]
# Motifs a developper : versions de node gerees par nvm/fnm, node_modules
# globaux dont le chemin depend de la version installee.
MOTIFS_CLAUDE = [
    "~/.nvm/versions/node/*/bin/claude",
    "~/.fnm/node-versions/*/installation/bin/claude",
    "~/Library/pnpm/claude",
]

# Message unique, reutilise par le lanceur et par la page web : la cause est
# la meme des deux cotes, seule la facon de la presenter change.
MESSAGE_CLAUDE_ABSENT = (
    "La lecture des factures s'appuie sur Claude Code en ligne de commande. "
    "Cette commande n'est pas installee sur ce Mac : l'application de bureau "
    "Claude ne la fournit pas. Pour l'installer, ouvre Terminal et lance "
    "curl -fsSL https://claude.ai/install.sh | bash, puis relance cette page. "
    "Le reste de la campagne (inventaire, controles, generation) fonctionne "
    "sans elle des lors que les factures ont deja ete lues.")


def _candidats_claude():
    """Chemins plausibles, dans l'ordre de preference. Ne teste rien."""
    for brut in EMPLACEMENTS_CLAUDE:
        yield os.path.expanduser(brut)
    for motif in MOTIFS_CLAUDE:
        # Les versions de node se trient mal en ASCII (10 avant 9) ; l'ordre
        # exact importe peu, n'importe laquelle fait l'affaire.
        for trouve in sorted(glob.glob(os.path.expanduser(motif)), reverse=True):
            yield trouve


def trouver_commande_claude():
    """Localise l'executable claude sans jamais ouvrir de dialogue.

    C'est la version utilisable depuis la page web : un serveur HTTP ne doit
    pas faire surgir une fenetre AppleScript sur le bureau, ni bloquer le
    thread qui sert la requete en attendant un clic. Renvoie None si rien
    n'est trouve -- a l'appelant de dire quoi faire.
    """
    chemin = shutil.which("claude")
    if chemin:
        return chemin
    # Chemin memorise lors d'un choix manuel precedent. isfile() seul ne
    # suffit pas : un PDF choisi par erreur dans le selecteur passe ce test
    # aussi. On verifie que c'est executable.
    if os.path.isfile(CONFIG_LOCAL):
        try:
            cfg = json.load(open(CONFIG_LOCAL, encoding="utf-8"))
            candidat = cfg.get("claude")
            if candidat and os.path.isfile(candidat) and os.access(candidat, os.X_OK):
                return candidat
        except Exception:
            pass
    for candidat in _candidats_claude():
        if os.path.isfile(candidat) and os.access(candidat, os.X_OK):
            return candidat
    return None


def resoudre_commande_claude():
    """Version interactive, pour le lanceur double-clic uniquement.

    Retombe sur un selecteur de fichier quand la recherche automatique
    echoue. La page web appelle trouver_commande_claude() a la place.
    """
    chemin = trouver_commande_claude()
    if chemin:
        return chemin
    alerte(MESSAGE_CLAUDE_ABSENT + " Si elle est deja installee a un endroit "
           "inhabituel, choisis l'executable claude dans la fenetre suivante "
           "(dans Terminal : which claude pour le retrouver).")
    chemin = choisir_fichier("Choisir l'executable claude")
    if not chemin:
        return None
    if not (os.path.isfile(chemin) and os.access(chemin, os.X_OK)):
        alerte("Ce fichier n'est pas un executable : %s" % chemin, critique=True)
        return None
    json.dump({"claude": chemin}, open(CONFIG_LOCAL, "w", encoding="utf-8"))
    return chemin


# ---------------------------------------------------------------------
# Dossiers clients (n'importe quelle societe presente sous dossiers/)
# ---------------------------------------------------------------------

def lister_dossiers():
    load_yaml()
    resultats = []
    if not os.path.isdir(DOSSIERS):
        return resultats
    for nom in sorted(os.listdir(DOSSIERS)):
        chemin = os.path.join(DOSSIERS, nom)
        soc = os.path.join(chemin, "societe.yaml")
        if os.path.isdir(chemin) and os.path.isfile(soc):
            try:
                conf = yaml.safe_load(open(soc, encoding="utf-8")) or {}
            except Exception:
                conf = {}
            resultats.append({
                "dossier": nom,
                "chemin": chemin,
                "code": conf.get("code", nom),
                "denomination": conf.get("denomination", nom),
            })
    return resultats


def charger_conf(dossier_abs):
    load_yaml()
    p = os.path.join(dossier_abs, "societe.yaml")
    if os.path.isfile(p):
        try:
            return yaml.safe_load(open(p, encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def referentiel_en_texte(dossier_abs):
    load_yaml()
    p = os.path.join(dossier_abs, "referentiel.yaml")
    if not os.path.isfile(p):
        return ""
    try:
        ref = yaml.safe_load(open(p, encoding="utf-8")) or {}
    except Exception:
        return ""
    if not ref:
        return ""
    return yaml.dump(ref, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------
# Etape 1 - inventaire (subprocess sur annexes_tva.py)
# ---------------------------------------------------------------------

def lancer_inventaire(dossier_abs, journal):
    cmd = [sys.executable, SCRIPT_ANNEXES, "inventaire", "--dossier", dossier_abs]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if r.stdout:
        journal.log(r.stdout.rstrip())
    if r.returncode != 0:
        journal.log("ERREUR : " + (r.stderr or "echec inconnu de l'inventaire").strip())
        return False
    return True


def lire_inventaire(dossier_abs):
    p = os.path.join(dossier_abs, "extraction", "_inventaire.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Etape 2 - lecture des factures via Claude Code (mode non interactif)
# ---------------------------------------------------------------------

def chemin_extraction(dossier_abs, doc):
    return os.path.join(dossier_abs, "extraction", doc["extraction"])


def deja_extrait(dossier_abs, doc):
    return os.path.exists(chemin_extraction(dossier_abs, doc))


def construire_prompt(doc, referentiel_txt, taux_admis):
    schema = """{
  "sens": "achat" ou "vente",
  "emetteur": "...", "emetteur_tva": "LUxxxxxxxx ou null",
  "destinataire": "...", "destinataire_tva": "LUxxxxxxxx ou null",
  "tiers": "nom court du tiers (l'autre partie que la societe elle-meme)",
  "tva_tiers": "numero de TVA du tiers, ou null",
  "pays": "code pays ISO2 du tiers",
  "num_facture": "tel qu'imprime sur la facture",
  "date": "AAAA-MM-JJ (date d'EMISSION de la facture)",
  "devise": "EUR ou autre code devise",
  "taux_change": 1.0,
  "total_ttc": 0.00,
  "regime": "un des regimes admis ci-dessous",
  "source_extraction": "texte ou vision selon la methode de lecture utilisee",
  "lignes": [ { "taux": 0.17, "base": 100.00, "tva": 17.00, "description": "..." } ]
}"""
    morceaux = [
        "Tu lis UNE SEULE facture pour l'extraire vers un JSON structure, "
        "dans un pipeline de declaration TVA luxembourgeoise.",
        "",
        "Fichier a lire (chemin absolu) : " + doc["chemin"],
        "",
        "Lis ce document (PDF ; texte natif ou scan/vision selon le fichier), "
        "puis reponds avec UN SEUL objet JSON, exactement ce schema, rien "
        "d'autre autour (pas de markdown, pas de phrase d'introduction) :",
        "",
        schema,
        "",
        "Regimes admis : " + ", ".join(REGIMES_ADMIS) + ".",
        "Taux de TVA admis au Luxembourg : "
        + ", ".join(str(t) for t in taux_admis) + ".",
        "La TVA reprise sur chaque ligne doit etre celle FACTUREE, "
        "pas un calcul base*taux.",
    ]
    if referentiel_txt:
        morceaux += [
            "",
            "Referentiel des tiers connus de cette societe (aide a la "
            "qualification, ne remplace pas ta lecture) :",
            referentiel_txt,
        ]
    morceaux += [
        "",
        "Si tu ne peux pas lire ce document de facon fiable (scan illisible, "
        "champ obligatoire absent, contenu contradictoire), NE DEVINE AUCUNE "
        "VALEUR : reponds uniquement, sur une seule ligne :",
        "ECHEC_LECTURE: <raison courte>",
    ]
    return "\n".join(morceaux)


def lire_facture_claude(commande_claude, doc, referentiel_txt, taux_admis, timeout=120):
    prompt = construire_prompt(doc, referentiel_txt, taux_admis)
    try:
        r = subprocess.run(
            [commande_claude, "-p", prompt] + FLAGS_LECTURE_SEULE,
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "commande claude introuvable : %s" % commande_claude
    except subprocess.TimeoutExpired:
        return None, "delai de lecture depasse"
    if r.returncode != 0:
        return None, "echec claude : %s" % (r.stderr.strip() or "erreur inconnue")
    return r.stdout.strip(), None


def extraire_json(reponse):
    if reponse.startswith("ECHEC_LECTURE"):
        raison = reponse.split(":", 1)[1].strip() if ":" in reponse else "lecture non fiable"
        return None, raison
    debut, fin = reponse.find("{"), reponse.rfind("}")
    if debut == -1 or fin == -1 or fin < debut:
        return None, "reponse non exploitable (pas de JSON)"
    try:
        doc = json.loads(reponse[debut:fin + 1])
    except json.JSONDecodeError as e:
        return None, "JSON invalide : %s" % e
    return doc, None


def valider_extraction(doc):
    manquants = [c for c in CHAMPS_REQUIS if doc.get(c) in (None, "")]
    if manquants:
        return "champ(s) manquant(s) : %s" % ", ".join(manquants)
    if doc["sens"] not in ("achat", "vente"):
        return "sens invalide : %r" % doc["sens"]
    if doc["regime"] not in REGIMES_ADMIS:
        return "regime invalide : %r" % doc["regime"]
    if not isinstance(doc["lignes"], list) or not doc["lignes"]:
        return "aucune ligne"
    for ligne in doc["lignes"]:
        if not all(k in ligne for k in ("taux", "base", "tva")):
            return "ligne incomplete"
    return None


def ecrire_a_verifier(dossier_abs, doc, raison):
    sha12 = doc["extraction"][:-len(".json")]
    p = os.path.join(dossier_abs, "extraction", "A_VERIFIER-%s.txt" % sha12)
    with open(p, "w", encoding="utf-8") as f:
        f.write("Fichier : %s\nChemin  : %s\nRaison  : %s\nDate    : %s\n"
                 % (doc["fichier"], doc["chemin"], raison,
                    dt.datetime.now().isoformat(timespec="seconds")))


# ---------------------------------------------------------------------
# Lecture par LOT
#
# Pourquoi : un appel a `claude -p`, meme trivial, coute ~41 000 tokens de
# mise en route (prompt systeme de l'agent, definitions d'outils, contexte)
# -- mesure faite le 08/09/2026 sur un appel "reponds OK". Le contenu utile
# d'une facture pese ~900 tokens. Lancer un appel PAR facture revient donc a
# payer 45 fois le contenant pour le contenu.
#
# Deux economies, cumulables :
#   1. grouper N factures dans un seul appel : le cout de mise en route est
#      paye une fois pour N au lieu de N fois ;
#   2. passer le TEXTE deja extrait par pdftotext a l'inventaire, au lieu de
#      donner un chemin et de laisser l'agent ouvrir le fichier lui-meme --
#      cela supprime un aller-retour d'outil par facture.
#
# Le repli est explicite : si la reponse d'un lot n'est pas exploitable, on
# reprend ce lot facture par facture. Un lot rate ne fait donc perdre que du
# temps, jamais une facture.
# ---------------------------------------------------------------------

TAILLE_LOT = 10

# ---------------------------------------------------------------------
# Documents ecartes de la lecture
#
# Un rappel de paiement, une relance ou un releve de compte n'est pas une
# facture : il ne cree aucun droit a deduction et ne porte pas de TVA a
# declarer. Le lire coute des jetons pour un resultat qui sera de toute
# facon rejete.
#
# PRUDENCE : ecarter a tort une VRAIE facture ferait sous-declarer, ce qui
# est bien plus grave que de lire un rappel pour rien. Deux garde-fous :
#   1. le motif doit apparaitre en TETE du document (les 800 premiers
#      caracteres), la ou vit le titre -- pas au detour d'une phrase du
#      genre "rappel de votre reference client" ;
#   2. le document ne doit porter AUCUNE marque de TVA. Un rappel qui
#      detaille une TVA est peut-etre une facture : on le lit.
# Et rien n'est silencieux : les documents ecartes sont listes avec leur
# motif, et peuvent etre reintegres d'un clic.
# ---------------------------------------------------------------------

MOTIFS_RAPPEL = re.compile(
    r"\b(rappel|relance|mise en demeure|reminder|payment reminder|"
    r"dunning|overdue|statement of account|releve de compte)\b", re.I)

# Marques d'une facture veritable : si elles sont presentes, on ne pretend
# pas trancher a la place du modele.
MARQUES_TVA = re.compile(
    r"\b(tva|vat|mwst|btw)\b|\b\d{1,2}[,.]\d{2}\s*%|\bLU\d{8}\b", re.I)

ENTETE = 800


def est_rappel(nom_fichier, texte):
    """(True, motif) si le document est un rappel a ecarter, sinon (False, None)."""
    tete = (texte or "")[:ENTETE]
    dans_nom = MOTIFS_RAPPEL.search(nom_fichier or "")
    dans_tete = MOTIFS_RAPPEL.search(tete)
    if not (dans_nom or dans_tete):
        return False, None
    # Un document sans texte (scan) ne peut etre juge que sur son nom : on
    # exige alors que le nom soit explicite, faute de pouvoir verifier.
    if texte and MARQUES_TVA.search(tete):
        return False, None
    trouve = (dans_nom or dans_tete).group(0)
    ou = "nom du fichier" if dans_nom else "en-tete du document"
    return True, "rappel / relance detecte (%s : « %s »), sans marque de TVA" % (ou, trouve)



def dossier_temporaire_utilisateur():
    """Dossier temporaire PROPRE A L'UTILISATEUR (/var/folders/.../T/).

    Pas /tmp : verifie le 08/09/2026, tesseract echoue a relire les images
    que ocrmypdf y depose ("image file not found"), alors que tout passe
    dans le dossier temporaire de l'utilisateur. macOS le publie via
    getconf ; TMPDIR le porte quand il est defini, ce qui n'est pas le cas
    d'un processus lance sans session (app du Finder, tache de fond).
    """
    depuis_env = os.environ.get("TMPDIR")
    if depuis_env and os.path.isdir(depuis_env):
        return depuis_env
    try:
        r = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"],
                            capture_output=True, text=True, timeout=10)
        chemin = r.stdout.strip()
        if r.returncode == 0 and chemin and os.path.isdir(chemin):
            return chemin
    except Exception:
        pass
    return tempfile.gettempdir()


def _pdftotext(chemin):
    """Texte d'un PDF, via poppler. None si l'outil manque ou echoue."""
    try:
        r = subprocess.run(["pdftotext", "-layout", chemin, "-"],
                            capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def ocr_disponible():
    return shutil.which("ocrmypdf") is not None


# Les factures luxembourgeoises arrivent en anglais, en francais ou en
# allemand -- parfois les trois dans un meme dossier. tesseract accepte
# plusieurs langues d'un coup ("eng+fra+deu") et choisit ligne par ligne ;
# le cout est un peu de temps, pas de justesse.
LANGUES_OCR = "eng+fra+deu"


def langues_ocr_disponibles(demandees=LANGUES_OCR):
    """Ne garde que les langues reellement installees.

    tesseract refuse de demarrer si UNE langue manque : demander deu sans
    le paquet de langues ferait echouer tout l'OCR, y compris l'anglais.
    """
    try:
        r = subprocess.run(["tesseract", "--list-langs"],
                            capture_output=True, text=True, timeout=30)
        presentes = {l.strip() for l in r.stdout.splitlines()[1:] if l.strip()}
    except Exception:
        return "eng"
    gardees = [l for l in demandees.split("+") if l in presentes]
    return "+".join(gardees) if gardees else "eng"


def ocriser(dossier_abs, doc, langues=None, timeout=300):
    """Passe un scan a l'OCR local et enregistre son texte.

    Pourquoi : un scan sans texte doit etre lu par VISION, le chemin de
    loin le plus cher et le plus lent -- et il ne peut pas rejoindre la
    lecture par lots, qui repose sur du texte. Un OCR local (ocrmypdf +
    tesseract) le ramene au regime texte pour quelques secondes de calcul
    sur cette machine, sans consommer un seul jeton.

    Le PDF produit est TEMPORAIRE : rien n'est ecrit dans le dossier du
    client, seul le texte est conserve dans l'espace de travail, a cote de
    ceux qu'a produits pdftotext.
    """
    source = doc.get("chemin")
    if not source or not os.path.isfile(source):
        return None, "fichier introuvable"
    dossier_textes = os.path.join(dossier_abs, "extraction", "textes")
    os.makedirs(dossier_textes, exist_ok=True)
    nom_texte = doc["sha256"][:12] + ".txt"
    cible_texte = os.path.join(dossier_textes, nom_texte)
    if os.path.isfile(cible_texte):
        return nom_texte, None      # deja ocerise lors d'un passage precedent

    langues = langues or langues_ocr_disponibles()
    temporaire = tempfile.mkstemp(suffix=".pdf",
                                   dir=dossier_temporaire_utilisateur())[1]
    try:
        # --force-ocr : certains scans portent une couche texte vide ou
        # fautive ; on la remplace au lieu de la conserver.
        # --optimize 0 : on ne garde pas le PDF, inutile de le compresser.
        # TMPDIR explicite : ocrmypdf echange ses images avec tesseract par
        # des fichiers temporaires. Lance depuis un contexte ou TMPDIR n'est
        # pas defini -- une app du Finder, une tache de fond -- tesseract
        # echoue avec "image file not found". Verifie le 08/09/2026 : c'est
        # la SEULE variable qui manquait.
        env = dict(os.environ)
        env.setdefault("TMPDIR", dossier_temporaire_utilisateur())
        r = subprocess.run(
            ["ocrmypdf", "--force-ocr", "--language", langues,
             "--optimize", "0", "--quiet", source, temporaire],
            capture_output=True, text=True, timeout=timeout, env=env)
        if r.returncode != 0:
            return None, "ocrmypdf : %s" % (r.stderr.strip()[:200] or "echec")
        texte = _pdftotext(temporaire)
        if not texte or len(texte.strip()) < 40:
            return None, "OCR sans resultat exploitable"
        with open(cible_texte, "w", encoding="utf-8") as f:
            f.write(texte)
        return nom_texte, None
    except subprocess.TimeoutExpired:
        return None, "delai d'OCR depasse"
    finally:
        try:
            os.remove(temporaire)
        except OSError:
            pass


def texte_extrait(dossier_abs, doc):
    """Texte produit par pdftotext a l'inventaire, ou None pour un scan."""
    if not doc.get("texte"):
        return None
    chemin = os.path.join(dossier_abs, "extraction", "textes", doc["texte"])
    if not os.path.isfile(chemin):
        return None
    try:
        with open(chemin, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def construire_prompt_lot(paires, referentiel_txt, taux_admis):
    """paires : [(doc, texte)]. Le referentiel et le schema ne sont ecrits
    qu'UNE fois pour tout le lot -- c'est la moitie de l'economie."""
    schema = """{
  "id": "l'identifiant du document, repris tel quel",
  "sens": "achat" ou "vente",
  "emetteur": "...", "emetteur_tva": "LUxxxxxxxx ou null",
  "destinataire": "...", "destinataire_tva": "LUxxxxxxxx ou null",
  "tiers": "nom court du tiers (l'autre partie que la societe elle-meme)",
  "tva_tiers": "numero de TVA du tiers, ou null",
  "pays": "code pays ISO2 du tiers",
  "num_facture": "tel qu'imprime sur la facture",
  "date": "AAAA-MM-JJ (date d'EMISSION de la facture)",
  "devise": "EUR ou autre code devise",
  "taux_change": 1.0,
  "total_ttc": 0.00,
  "regime": "un des regimes admis ci-dessous",
  "source_extraction": "texte",
  "lignes": [ { "taux": 0.17, "base": 100.00, "tva": 17.00, "description": "..." } ]
}"""
    morceaux = [
        "Tu extrais %d factures vers du JSON structure, dans un pipeline de "
        "declaration TVA luxembourgeoise." % len(paires),
        "",
        "Le texte de chaque document t'est donne ci-dessous : n'ouvre AUCUN "
        "fichier, tout est deja la.",
        "",
        "Reponds avec UN SEUL tableau JSON, un objet par document, dans "
        "l'ordre, rien d'autre autour (pas de markdown, pas d'introduction). "
        "Chaque objet suit exactement ce schema :",
        "",
        schema,
        "",
        "Regimes admis : " + ", ".join(REGIMES_ADMIS) + ".",
        "Taux de TVA admis au Luxembourg : "
        + ", ".join(str(x) for x in taux_admis) + ".",
        "La TVA reprise sur chaque ligne doit etre celle FACTUREE, "
        "pas un calcul base*taux.",
        "",
        "Pour un document que tu ne peux pas extraire de facon fiable (champ "
        "obligatoire absent, contenu contradictoire, piece qui n'est pas une "
        "facture), NE DEVINE AUCUNE VALEUR : mets pour ce document un objet "
        '{\"id\": \"...\", \"echec\": \"raison courte\"}. Les autres '
        "documents du lot doivent quand meme etre extraits.",
    ]
    if referentiel_txt:
        morceaux += ["", "Referentiel des tiers connus de cette societe (aide a "
                      "la qualification, ne remplace pas ta lecture) :",
                      referentiel_txt]
    for doc, texte in paires:
        morceaux += ["", "=" * 60,
                      "DOCUMENT id=%s  (nom du fichier : %s)" % (doc["sha256"][:12],
                                                                  doc["fichier"]),
                      "=" * 60, texte]
    return "\n".join(morceaux)


def lire_lot_claude(commande_claude, paires, referentiel_txt, taux_admis,
                     timeout=600):
    prompt = construire_prompt_lot(paires, referentiel_txt, taux_admis)
    try:
        r = subprocess.run([commande_claude, "-p", prompt],
                            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "commande claude introuvable : %s" % commande_claude
    except subprocess.TimeoutExpired:
        return None, "delai de lecture du lot depasse"
    if r.returncode != 0:
        return None, "echec claude : %s" % (r.stderr.strip() or "erreur inconnue")
    return r.stdout.strip(), None


def extraire_tableau(reponse):
    """Le tableau JSON de la reponse, indexe par id."""
    debut, fin = reponse.find("["), reponse.rfind("]")
    if debut == -1 or fin == -1 or fin < debut:
        return None, "reponse non exploitable (pas de tableau JSON)"
    try:
        objets = json.loads(reponse[debut:fin + 1])
    except json.JSONDecodeError as e:
        return None, "JSON invalide : %s" % e
    if not isinstance(objets, list):
        return None, "la reponse n'est pas un tableau"
    return {str(o.get("id")): o for o in objets if isinstance(o, dict)}, None


def traiter_lot(commande_claude, dossier_abs, docs, referentiel_txt, taux_admis,
                 journal):
    """Traite un lot en un appel. Renvoie (n_lues, n_a_verifier).

    Repli : si la reponse du lot est inexploitable, le lot est repris facture
    par facture -- plus cher, mais aucune facture n'est perdue.
    """
    paires = []
    for doc in docs:
        texte = texte_extrait(dossier_abs, doc)
        if texte:
            paires.append((doc, texte))
    # Les scans n'ont pas de texte : ils gardent la lecture individuelle,
    # par vision, tant qu'ils ne sont pas passes a l'OCR.
    sans_texte = [d for d in docs if not any(d is p[0] for p in paires)]

    lues = a_verifier = 0
    if paires:
        reponse, err = lire_lot_claude(commande_claude, paires, referentiel_txt,
                                        taux_admis)
        par_id, err2 = (None, err) if err else extraire_tableau(reponse)
        if err2:
            journal.log("    lot inexploitable (%s) — reprise une par une" % err2)
            for doc, _ in paires:
                if traiter_document(commande_claude, dossier_abs, doc,
                                     referentiel_txt, taux_admis, journal):
                    lues += 1
                else:
                    a_verifier += 1
        else:
            for doc, _ in paires:
                objet = par_id.get(doc["sha256"][:12])
                if objet is None:
                    raison = "absent de la reponse du lot"
                elif objet.get("echec"):
                    raison = str(objet["echec"])
                else:
                    raison = valider_extraction(objet)
                    raison = ("reponse invalide : %s" % raison) if raison else None
                if raison:
                    ecrire_a_verifier(dossier_abs, doc, raison)
                    journal.log("    a verifier : %s (%s)" % (doc["fichier"], raison))
                    a_verifier += 1
                else:
                    objet.pop("id", None)
                    with open(chemin_extraction(dossier_abs, doc), "w",
                               encoding="utf-8") as f:
                        json.dump(objet, f, ensure_ascii=False, indent=2)
                    journal.log("    lu : %s" % doc["fichier"])
                    lues += 1
    for doc in sans_texte:
        if traiter_document(commande_claude, dossier_abs, doc, referentiel_txt,
                             taux_admis, journal):
            lues += 1
        else:
            a_verifier += 1
    return lues, a_verifier


def traiter_document(commande_claude, dossier_abs, doc, referentiel_txt, taux_admis, journal):
    """Lit une facture via Claude Code et n'ecrit son JSON que si la reponse
    est exploitable ; renvoie True si lu, False si mis de cote (A_VERIFIER)."""
    fichier = doc["fichier"]
    extrait, err = None, None
    reponse, err = lire_facture_claude(commande_claude, doc, referentiel_txt, taux_admis)
    if err is None:
        extrait, err = extraire_json(reponse)
    if err is None:
        invalide = valider_extraction(extrait)
        if invalide:
            err = "reponse invalide : %s" % invalide
    if err:
        ecrire_a_verifier(dossier_abs, doc, err)
        journal.log("    a verifier : %s (%s)" % (fichier, err))
        return False
    with open(chemin_extraction(dossier_abs, doc), "w", encoding="utf-8") as f:
        json.dump(extrait, f, ensure_ascii=False, indent=2)
    journal.log("    lu : %s" % fichier)
    return True


# ---------------------------------------------------------------------
# Etape 3 - generation des annexes (subprocess interactif, via pty)
# ---------------------------------------------------------------------

def executer_annexes_interactif(dossier_abs, periode, journal):
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, SCRIPT_ANNEXES, "annexes", "--dossier", dossier_abs,
         "--periode", periode],
        cwd=REPO, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    depuis_derniere_reponse = b""
    dernier_octet = time.time()
    try:
        while True:
            prets, _, _ = select.select([master], [], [], 0.3)
            if master in prets:
                try:
                    morceau = os.read(master, 4096)
                except OSError:
                    morceau = b""
                if not morceau:
                    break
                depuis_derniere_reponse += morceau
                dernier_octet = time.time()
                while b"\n" in depuis_derniere_reponse:
                    ligne, depuis_derniere_reponse = depuis_derniere_reponse.split(b"\n", 1)
                    texte_ligne = ligne.decode("utf-8", "replace")
                    if texte_ligne.strip():
                        journal.log("  " + texte_ligne)
            else:
                if proc.poll() is not None:
                    break
                texte = depuis_derniere_reponse.decode("utf-8", "replace")
                if (texte.strip() and PROMPT_ATTENTE.search(texte)
                        and (time.time() - dernier_octet) > 0.3):
                    m = re.search(r"\[([^\]\n]*)\]\s*:?\s*$", texte.strip())
                    defaut = m.group(1) if m else ""
                    reponse = demander_texte(texte, defaut)
                    if reponse is None:
                        reponse = defaut
                        journal.log("  (dialogue annule, valeur par defaut retenue : %s)"
                                    % defaut)
                    os.write(master, (reponse + "\n").encode("utf-8"))
                    depuis_derniere_reponse = b""
    finally:
        os.close(master)
    proc.wait()
    return proc.returncode


def dernier_declare(dossier_sortie):
    fichiers = sorted(glob.glob(os.path.join(dossier_sortie, "*-DECLARE.json")),
                       key=os.path.getmtime, reverse=True)
    if not fichiers:
        return None
    with open(fichiers[0], encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Journal (log lisible, ecrit dans le dossier de sortie de la campagne)
# ---------------------------------------------------------------------

class Journal:
    def __init__(self, chemin):
        self.f = open(chemin, "a", encoding="utf-8")

    def log(self, msg=""):
        print(msg)
        self.f.write(msg + "\n")
        self.f.flush()

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# Orchestration complete (lineaire -- pas de fil separe, pas de GUI)
# ---------------------------------------------------------------------

def executer_pipeline(societe, periode, commande_claude):
    dossier_abs = societe["chemin"]
    conf = charger_conf(dossier_abs)
    dossier_sortie = conf.get("dossier_sortie") or os.path.join(dossier_abs, "annexes")
    dossier_sortie = os.path.abspath(os.path.expanduser(dossier_sortie))
    os.makedirs(dossier_sortie, exist_ok=True)
    horodatage = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    journal = Journal(os.path.join(dossier_sortie, "CAMPAGNE-%s-%s.log" % (periode, horodatage)))
    try:
        journal.log("Societe : %s (%s)" % (societe["denomination"], societe["code"]))
        journal.log("Periode : %s" % periode)
        journal.log("")

        journal.log("Etape 1/3 - inventaire")
        if not lancer_inventaire(dossier_abs, journal):
            alerte("L'inventaire a echoue. Voir le journal dans le dossier "
                   "des annexes pour le detail.", critique=True)
            return
        inv = lire_inventaire(dossier_abs)
        natifs = sum(1 for d in inv["documents"] if d["type"] == "natif")
        scans = len(inv["documents"]) - natifs
        msg_inv = ("%d document(s) unique(s), %d doublon(s) ecarte(s), "
                    "%d non-PDF ignore(s) -- natifs : %d, scans : %d"
                    % (inv["nb_fichiers"], inv["nb_doublons_ecartes"],
                       inv["nb_non_pdf_ignores"], natifs, scans))
        journal.log("  " + msg_inv)
        notifier(msg_inv, titre="Inventaire termine")

        journal.log("")
        journal.log("Etape 2/3 - lecture des factures")
        a_lire = [d for d in inv["documents"] if not deja_extrait(dossier_abs, d)]
        if a_lire:
            notifier("Lecture de %d facture(s) en cours..." % len(a_lire))
        else:
            journal.log("  toutes les factures ont deja une extraction.")
        referentiel_txt = referentiel_en_texte(dossier_abs)
        taux_admis = conf.get("taux_admis", [0.0, 0.03, 0.08, 0.14, 0.17])
        n_lues = n_a_verifier = 0
        for i, doc in enumerate(a_lire, 1):
            journal.log("  [%d/%d] %s" % (i, len(a_lire), doc["fichier"]))
            if traiter_document(commande_claude, dossier_abs, doc, referentiel_txt,
                                 taux_admis, journal):
                n_lues += 1
            else:
                n_a_verifier += 1
        if a_lire:
            notifier("Lecture terminee : %d lue(s), %d a verifier" % (n_lues, n_a_verifier),
                      titre="Lecture terminee")

        journal.log("")
        journal.log("Etape 3/3 - generation des annexes")
        code = executer_annexes_interactif(dossier_abs, periode, journal)
        if code != 0:
            alerte("La generation des annexes a echoue (code %d). Voir le "
                   "journal dans le dossier des annexes pour le detail." % code,
                   critique=True)
            return

        declare = dernier_declare(dossier_sortie)
        if declare is None:
            alerte("Annexes generees mais aucun instantane -DECLARE.json "
                   "retrouve.", critique=True)
            return

        resume = (
            "Lignes retenues : %d\n"
            "Exceptions bloquantes : %d\n"
            "TVA amont : %.2f\n"
            "TVA deductible : %.2f"
            % (len(declare["lignes"]), declare["exceptions_bloquantes"],
               declare["totaux"]["tva_amont"], declare["totaux"]["tva_deductible"]))
        journal.log("")
        journal.log(resume.replace("\n", "  |  "))
        if declare["exceptions_bloquantes"]:
            resume += ("\n\n%d exception(s) bloquante(s) -- voir le rapport "
                        "dans le dossier des annexes avant de deposer quoi "
                        "que ce soit." % declare["exceptions_bloquantes"])
        alerte(resume, titre="Campagne terminee")
        try:
            subprocess.run(["open", dossier_sortie])
        except Exception:
            pass
    except Exception as e:
        journal.log("ERREUR INATTENDUE : %s" % e)
        alerte("Erreur inattendue : %s" % e, critique=True)
    finally:
        journal.close()


# ---------------------------------------------------------------------
# Les 3 questions posees au demarrage
# ---------------------------------------------------------------------

def ecran_societe(societes):
    items = ["%s (%s)" % (s["denomination"], s["code"]) for s in societes]
    choix = choisir_dans_liste(items, "Quelle societe traiter ?")
    if choix is None:
        return None
    return societes[items.index(choix)]


def ecran_type():
    libelles = {"Mensuelle": "1", "Trimestrielle": "2", "Annuelle": "3"}
    choix = choisir_dans_liste(list(libelles.keys()), "Quel type de declaration ?")
    if choix is None:
        return None
    return libelles[choix]


def ecran_periode(type_decl):
    aujourdhui = dt.date.today()
    annee = demander_texte("Annee ?", str(aujourdhui.year))
    if annee is None:
        return None
    annee = annee.strip()
    if not re.fullmatch(r"\d{4}", annee):
        alerte("Annee sur 4 chiffres attendue.", critique=True)
        return None
    if type_decl == "1":
        mois = demander_texte("Mois (1-12) ?", str(aujourdhui.month))
        if mois is None:
            return None
        mois = mois.strip()
        if not re.fullmatch(r"([1-9]|1[0-2])", mois):
            alerte("Mois entre 1 et 12 attendu.", critique=True)
            return None
        return "%s-M%02d" % (annee, int(mois))
    elif type_decl == "2":
        defaut_q = str((aujourdhui.month - 1) // 3 + 1)
        trimestre = demander_texte("Trimestre (1-4) ?", defaut_q)
        if trimestre is None:
            return None
        trimestre = trimestre.strip()
        if trimestre not in ("1", "2", "3", "4"):
            alerte("Trimestre entre 1 et 4 attendu.", critique=True)
            return None
        return "%s-Q%s" % (annee, trimestre)
    else:
        return "%s-ANNUAL" % annee


def main():
    if not ensure_dependencies():
        return
    societes = lister_dossiers()
    if not societes:
        alerte("Aucun dossier avec societe.yaml trouve sous dossiers/.", critique=True)
        return
    societe = ecran_societe(societes)
    if societe is None:
        return
    type_decl = ecran_type()
    if type_decl is None:
        return
    periode = ecran_periode(type_decl)
    if periode is None:
        return
    commande_claude = resoudre_commande_claude()
    if not commande_claude:
        alerte("La lecture des factures a besoin de la commande claude. "
               "Campagne annulee.", critique=True)
        return
    executer_pipeline(societe, periode, commande_claude)


if __name__ == "__main__":
    main()
