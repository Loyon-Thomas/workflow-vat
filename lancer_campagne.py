#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lanceur d'une campagne TVA complete : inventaire, lecture des factures
(Claude Code, mode non interactif), generation des annexes.

Pilote src/annexes_tva.py par sa ligne de commande, sans jamais le modifier.
Aucun calcul TVA n'est refait ici : ce fichier orchestre, lit et affiche.
"""
import datetime as dt
import glob
import importlib.util
import json
import os
import pty
import queue
import re
import select
import shutil
import subprocess
import sys
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext, filedialog
except ImportError:
    subprocess.run(["osascript", "-e",
        'display alert "Python sans Tkinter" message '
        '"Ce Python n\'a pas Tkinter (interface graphique). '
        'Installe Python depuis python.org (inclut Tk), puis relance." '
        'as critical'])
    sys.exit(1)

REPO = os.path.dirname(os.path.abspath(__file__))
DOSSIERS = os.path.join(REPO, "dossiers")
SCRIPT_ANNEXES = os.path.join(REPO, "src", "annexes_tva.py")
CONFIG_LOCAL = os.path.expanduser("~/.campagne_tva_lanceur.json")

# Flag de lecture seule pour l'invocation de Claude Code en mode non
# interactif : a verifier sur la machine cible (`claude -p --help`), ce
# binaire n'existe pas dans l'environnement ou ce fichier a ete ecrit.
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
# Dependances (pyyaml, openpyxl) deja requises par les scripts proteges
# ---------------------------------------------------------------------

def ensure_dependencies(root):
    manquants = [m for m in ("yaml", "openpyxl") if not module_present(m)]
    if not manquants:
        return True
    paquets = {"yaml": "pyyaml", "openpyxl": "openpyxl"}
    liste = ", ".join(paquets[m] for m in manquants)
    ok = messagebox.askyesno(
        "Dependances manquantes",
        "Les scripts de ce dossier necessitent %s, absent(s) de ce Python.\n\n"
        "Installer maintenant (pip3, en arriere-plan) ?" % liste)
    if not ok:
        messagebox.showerror(
            "Installation refusee",
            "Sans ces paquets, la campagne ne peut pas etre generee.\n"
            "Installation manuelle : pip3 install %s" % liste)
        return False
    prog = tk.Toplevel(root)
    prog.title("Installation en cours")
    tk.Label(prog, text="Installation de %s..." % liste, padx=24, pady=24).pack()
    prog.update()
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet"] +
                        [paquets[m] for m in manquants],
                        check=True, capture_output=True, text=True, timeout=180)
    except subprocess.CalledProcessError as e:
        prog.destroy()
        messagebox.showerror("Echec de l'installation", e.stderr or str(e))
        return False
    except subprocess.TimeoutExpired:
        prog.destroy()
        messagebox.showerror("Echec de l'installation", "Delai depasse.")
        return False
    prog.destroy()
    importlib.invalidate_caches()
    encore_manquants = [m for m in manquants if not module_present(m)]
    if encore_manquants:
        messagebox.showerror("Installation incomplete",
            "Toujours absent : %s" % ", ".join(encore_manquants))
        return False
    return True


def resoudre_commande_claude():
    chemin = shutil.which("claude")
    if chemin:
        return chemin
    if os.path.isfile(CONFIG_LOCAL):
        try:
            cfg = json.load(open(CONFIG_LOCAL, encoding="utf-8"))
            if cfg.get("claude") and os.path.isfile(cfg["claude"]):
                return cfg["claude"]
        except Exception:
            pass
    messagebox.showinfo(
        "Localiser Claude Code",
        "La commande 'claude' n'a pas ete trouvee automatiquement "
        "(cela arrive quand elle a ete installee depuis un terminal).\n\n"
        "Dans la fenetre suivante, choisis l'executable claude.\n"
        "Pour le retrouver : ouvrir Terminal, taper 'which claude'.")
    chemin = filedialog.askopenfilename(title="Choisir l'executable claude")
    if not chemin:
        return None
    json.dump({"claude": chemin}, open(CONFIG_LOCAL, "w", encoding="utf-8"))
    return chemin


# ---------------------------------------------------------------------
# Dossiers clients
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

def lancer_inventaire(dossier_abs, ui):
    cmd = [sys.executable, SCRIPT_ANNEXES, "inventaire", "--dossier", dossier_abs]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        ui.error((r.stderr or r.stdout or "echec inconnu de l'inventaire").strip())
        return False
    return True


def lire_inventaire(dossier_abs):
    p = os.path.join(dossier_abs, "extraction", "_inventaire.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def afficher_compteurs_inventaire(inv, ui):
    natifs = sum(1 for d in inv["documents"] if d["type"] == "natif")
    scans = len(inv["documents"]) - natifs
    ui.log("  %d document(s) unique(s), %d doublon(s) ecarte(s), "
           "%d fichier(s) non-PDF ignore(s)"
           % (inv["nb_fichiers"], inv["nb_doublons_ecartes"], inv["nb_non_pdf_ignores"]))
    ui.log("  natifs : %d   scans (lecture vision) : %d" % (natifs, scans))


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


def traiter_document(commande_claude, dossier_abs, doc, referentiel_txt, taux_admis, ui):
    fichier = doc["fichier"]
    reponse, err = lire_facture_claude(commande_claude, doc, referentiel_txt, taux_admis)
    if err is None:
        extrait, err = extraire_json(reponse)
    if err:
        ecrire_a_verifier(dossier_abs, doc, err)
        ui.log("    a verifier : %s (%s)" % (fichier, err))
        return
    err = valider_extraction(extrait)
    if err:
        ecrire_a_verifier(dossier_abs, doc, "reponse invalide : %s" % err)
        ui.log("    a verifier : %s (reponse invalide : %s)" % (fichier, err))
        return
    with open(chemin_extraction(dossier_abs, doc), "w", encoding="utf-8") as f:
        json.dump(extrait, f, ensure_ascii=False, indent=2)
    ui.log("    lu : %s" % fichier)


# ---------------------------------------------------------------------
# Etape 3 - generation des annexes (subprocess interactif, via pty)
# ---------------------------------------------------------------------

def executer_annexes_interactif(dossier_abs, periode, ui):
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, SCRIPT_ANNEXES, "annexes", "--dossier", dossier_abs,
         "--periode", periode],
        cwd=REPO, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    journal = b""
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
                journal += morceau
                depuis_derniere_reponse += morceau
                dernier_octet = time.time()
                while b"\n" in depuis_derniere_reponse:
                    ligne, depuis_derniere_reponse = depuis_derniere_reponse.split(b"\n", 1)
                    texte_ligne = ligne.decode("utf-8", "replace")
                    if texte_ligne.strip():
                        ui.log("  " + texte_ligne)
            else:
                if proc.poll() is not None:
                    break
                texte = depuis_derniere_reponse.decode("utf-8", "replace")
                if (texte.strip() and PROMPT_ATTENTE.search(texte)
                        and (time.time() - dernier_octet) > 0.3):
                    reponse = ui.demander_texte_invite(texte)
                    os.write(master, (reponse + "\n").encode("utf-8"))
                    depuis_derniere_reponse = b""
    finally:
        os.close(master)
    proc.wait()
    return proc.returncode, journal.decode("utf-8", "replace")


def dernier_declare(dossier_sortie):
    fichiers = sorted(glob.glob(os.path.join(dossier_sortie, "*-DECLARE.json")),
                       key=os.path.getmtime, reverse=True)
    if not fichiers:
        return None
    with open(fichiers[0], encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Orchestration complete (tourne dans un thread de fond)
# ---------------------------------------------------------------------

class PipelineUI:
    """Pont thread-sur : le pipeline tourne en arriere-plan, l'affichage et
    les questions passent par une file consommee par le thread principal
    Tkinter (root.after)."""

    def __init__(self, out_queue):
        self.out = out_queue

    def log(self, msg):
        self.out.put(("log", msg))

    def error(self, msg):
        self.out.put(("error", msg))

    def demander_texte_invite(self, contexte):
        rep_q = queue.Queue()
        self.out.put(("ask_prorata", contexte, rep_q))
        return rep_q.get()

    def resume(self, data):
        self.out.put(("done", data))


def executer_pipeline(societe, periode, commande_claude, ui):
    try:
        dossier_abs = societe["chemin"]
        conf = charger_conf(dossier_abs)
        ui.log("Societe : %s (%s)" % (societe["denomination"], societe["code"]))
        ui.log("Periode : %s" % periode)
        ui.log("")

        ui.log("Etape 1/3 - inventaire")
        if not lancer_inventaire(dossier_abs, ui):
            ui.resume({"erreur": "l'inventaire a echoue (voir ci-dessus)"})
            return
        inv = lire_inventaire(dossier_abs)
        afficher_compteurs_inventaire(inv, ui)

        ui.log("")
        ui.log("Etape 2/3 - lecture des factures")
        a_lire = [d for d in inv["documents"] if not deja_extrait(dossier_abs, d)]
        if not a_lire:
            ui.log("  toutes les factures ont deja une extraction.")
        referentiel_txt = referentiel_en_texte(dossier_abs)
        taux_admis = conf.get("taux_admis", [0.0, 0.03, 0.08, 0.14, 0.17])
        for i, doc in enumerate(a_lire, 1):
            ui.log("  [%d/%d] %s" % (i, len(a_lire), doc["fichier"]))
            traiter_document(commande_claude, dossier_abs, doc, referentiel_txt,
                              taux_admis, ui)

        ui.log("")
        ui.log("Etape 3/3 - generation des annexes")
        code, _ = executer_annexes_interactif(dossier_abs, periode, ui)
        if code != 0:
            ui.resume({"erreur": "la generation des annexes a echoue (code %d)" % code})
            return

        dossier_sortie = conf.get("dossier_sortie") or os.path.join(dossier_abs, "annexes")
        dossier_sortie = os.path.abspath(os.path.expanduser(dossier_sortie))
        declare = dernier_declare(dossier_sortie)
        if declare is None:
            ui.resume({"erreur": "annexes generees mais aucun instantane "
                                  "-DECLARE.json retrouve dans %s" % dossier_sortie})
            return
        ui.resume({
            "lignes": len(declare["lignes"]),
            "exceptions_bloquantes": declare["exceptions_bloquantes"],
            "tva_amont": declare["totaux"]["tva_amont"],
            "tva_deductible": declare["totaux"]["tva_deductible"],
            "dossier_sortie": dossier_sortie,
        })
    except Exception as e:
        ui.resume({"erreur": "erreur inattendue : %s" % e})


# ---------------------------------------------------------------------
# Interface graphique
# ---------------------------------------------------------------------

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Campagne TVA")
        self.root.geometry("680x520")
        self.societes = lister_dossiers()
        self.type_var = tk.StringVar(value="2")
        aujourdhui = dt.date.today()
        self.annee_var = tk.StringVar(value=str(aujourdhui.year))
        self.mois_var = tk.StringVar(value=str(aujourdhui.month))
        self.trimestre_var = tk.StringVar(value=str((aujourdhui.month - 1) // 3 + 1))
        self.societe_selectionnee = None
        self.frame = None
        self.journal = None
        self.out_queue = None
        self.ecran_societe()
        self.root.mainloop()

    def _nouvel_ecran(self):
        if self.frame is not None:
            self.frame.destroy()
        self.frame = ttk.Frame(self.root, padding=24)
        self.frame.pack(fill="both", expand=True)
        return self.frame

    # -- ecran 1 : societe -------------------------------------------------
    def ecran_societe(self):
        f = self._nouvel_ecran()
        ttk.Label(f, text="Quelle societe traiter ?", font=("", 14, "bold")).pack(
            anchor="w", pady=(0, 12))
        if not self.societes:
            ttk.Label(f, text="Aucun dossier avec societe.yaml sous dossiers/.").pack(
                anchor="w")
            return
        var = tk.StringVar(value=self.societes[0]["dossier"])
        for s in self.societes:
            ttk.Radiobutton(f, text="%s (%s)" % (s["denomination"], s["code"]),
                             variable=var, value=s["dossier"]).pack(anchor="w", pady=2)

        def suivant():
            self.societe_selectionnee = next(
                s for s in self.societes if s["dossier"] == var.get())
            self.ecran_type()

        ttk.Button(f, text="Suivant", command=suivant).pack(anchor="e", pady=(24, 0))

    # -- ecran 2 : type de declaration -------------------------------------
    def ecran_type(self):
        f = self._nouvel_ecran()
        ttk.Label(f, text="Type de declaration ?", font=("", 14, "bold")).pack(
            anchor="w", pady=(0, 12))
        for valeur, libelle in (("1", "Mensuelle"), ("2", "Trimestrielle"),
                                 ("3", "Annuelle")):
            ttk.Radiobutton(f, text=libelle, variable=self.type_var,
                             value=valeur).pack(anchor="w", pady=2)
        ttk.Button(f, text="Suivant", command=self.ecran_periode).pack(
            anchor="e", pady=(24, 0))

    # -- ecran 3 : periode --------------------------------------------------
    def ecran_periode(self):
        f = self._nouvel_ecran()
        ttk.Label(f, text="Periode ?", font=("", 14, "bold")).pack(
            anchor="w", pady=(0, 12))
        ligne = ttk.Frame(f)
        ligne.pack(anchor="w", pady=4)
        ttk.Label(ligne, text="Annee : ").pack(side="left")
        ttk.Entry(ligne, textvariable=self.annee_var, width=8).pack(side="left")
        t = self.type_var.get()
        if t == "1":
            ligne2 = ttk.Frame(f)
            ligne2.pack(anchor="w", pady=4)
            ttk.Label(ligne2, text="Mois (1-12) : ").pack(side="left")
            ttk.Entry(ligne2, textvariable=self.mois_var, width=5).pack(side="left")
        elif t == "2":
            ligne2 = ttk.Frame(f)
            ligne2.pack(anchor="w", pady=4)
            ttk.Label(ligne2, text="Trimestre (1-4) : ").pack(side="left")
            ttk.Entry(ligne2, textvariable=self.trimestre_var, width=5).pack(side="left")
        ttk.Button(f, text="Lancer la campagne", command=self.lancer).pack(
            anchor="e", pady=(24, 0))

    def construire_periode(self):
        annee = self.annee_var.get().strip()
        if not re.fullmatch(r"\d{4}", annee):
            messagebox.showerror("Annee invalide", "Annee sur 4 chiffres attendue.")
            return None
        t = self.type_var.get()
        if t == "1":
            mois = self.mois_var.get().strip()
            if not re.fullmatch(r"([1-9]|1[0-2])", mois):
                messagebox.showerror("Mois invalide", "Mois entre 1 et 12 attendu.")
                return None
            return "%s-M%02d" % (annee, int(mois))
        elif t == "2":
            trimestre = self.trimestre_var.get().strip()
            if trimestre not in ("1", "2", "3", "4"):
                messagebox.showerror("Trimestre invalide", "Trimestre entre 1 et 4 attendu.")
                return None
            return "%s-Q%s" % (annee, trimestre)
        else:
            return "%s-ANNUAL" % annee

    # -- lancement ------------------------------------------------------
    def lancer(self):
        periode = self.construire_periode()
        if periode is None:
            return
        if not ensure_dependencies(self.root):
            return
        commande_claude = resoudre_commande_claude()
        if not commande_claude:
            messagebox.showerror("Claude Code requis",
                "La lecture des factures a besoin de la commande claude. "
                "Campagne annulee.")
            return
        self.ecran_suivi()
        self.out_queue = queue.Queue()
        ui = PipelineUI(self.out_queue)
        threading.Thread(
            target=executer_pipeline,
            args=(self.societe_selectionnee, periode, commande_claude, ui),
            daemon=True).start()
        self.root.after(150, self.poll_queue)

    # -- ecran de suivi ---------------------------------------------------
    def ecran_suivi(self):
        f = self._nouvel_ecran()
        ttk.Label(f, text="Campagne en cours...", font=("", 14, "bold")).pack(
            anchor="w", pady=(0, 12))
        self.journal = scrolledtext.ScrolledText(f, width=84, height=24, state="disabled")
        self.journal.pack(fill="both", expand=True)

    def ecrire_journal(self, msg):
        self.journal.configure(state="normal")
        self.journal.insert("end", msg + "\n")
        self.journal.see("end")
        self.journal.configure(state="disabled")

    def poll_queue(self):
        try:
            while True:
                item = self.out_queue.get_nowait()
                genre = item[0]
                if genre == "log":
                    self.ecrire_journal(item[1])
                elif genre == "error":
                    self.ecrire_journal("ERREUR : " + item[1])
                elif genre == "ask_prorata":
                    _, contexte, rep_q = item
                    self.ouvrir_dialogue_invite(contexte, rep_q)
                elif genre == "done":
                    self.afficher_resume(item[1])
                    return
        except queue.Empty:
            pass
        self.root.after(150, self.poll_queue)

    def ouvrir_dialogue_invite(self, contexte, rep_q):
        top = tk.Toplevel(self.root)
        top.title("Reponse necessaire")
        top.transient(self.root)
        top.grab_set()
        zone = scrolledtext.ScrolledText(top, width=76, height=10, wrap="word")
        zone.insert("1.0", contexte)
        zone.configure(state="disabled")
        zone.pack(padx=12, pady=(12, 6))
        m = re.search(r"\[([^\]\n]*)\]\s*:?\s*$", contexte.strip())
        defaut = m.group(1) if m else ""
        var = tk.StringVar(value=defaut)
        entree = ttk.Entry(top, textvariable=var, width=30)
        entree.pack(padx=12, pady=(0, 12))
        entree.focus_set()
        entree.selection_range(0, "end")

        def valider(event=None):
            rep_q.put(var.get())
            top.destroy()

        ttk.Button(top, text="Valider", command=valider).pack(pady=(0, 12))
        top.bind("<Return>", valider)

    def afficher_resume(self, data):
        if "erreur" in data:
            self.ecrire_journal("")
            self.ecrire_journal("Campagne interrompue : %s" % data["erreur"])
            messagebox.showerror("Campagne interrompue", data["erreur"])
            return
        self.ecrire_journal("")
        self.ecrire_journal("=" * 44)
        self.ecrire_journal("Lignes retenues        : %d" % data["lignes"])
        self.ecrire_journal("Exceptions bloquantes   : %d" % data["exceptions_bloquantes"])
        self.ecrire_journal("TVA amont               : %.2f" % data["tva_amont"])
        self.ecrire_journal("TVA deductible          : %.2f" % data["tva_deductible"])
        self.ecrire_journal("=" * 44)
        if data["exceptions_bloquantes"]:
            messagebox.showwarning(
                "Exceptions bloquantes",
                "%d exception(s) bloquante(s) — voir le rapport dans le "
                "dossier des annexes avant de deposer quoi que ce soit."
                % data["exceptions_bloquantes"])
        try:
            subprocess.run(["open", data["dossier_sortie"]])
        except Exception:
            pass


def main():
    precoce = tk.Tk()
    precoce.withdraw()
    ok = ensure_dependencies(precoce)
    precoce.destroy()
    if not ok:
        sys.exit(1)
    App()


if __name__ == "__main__":
    main()
