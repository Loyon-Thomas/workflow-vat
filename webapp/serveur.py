#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Page locale de campagne TVA -- ecrans 1 (importation) et 2 (detection).

Sert une page HTML et une API JSON, en local uniquement (liaison a
127.0.0.1, jamais accessible depuis l'exterieur de la machine). N'effectue
aucun calcul : appelle src/annexes_tva.py exactement comme lancer_campagne.py,
sans jamais le modifier. Reutilise les fonctions deja ecrites et testees de
lancer_campagne.py plutot que de les dupliquer.

Ecran 2 : les controles (sens, doublons, coherence, recurrence C8...) ne sont
produits que par l'etape "annexes" du moteur, qui exige une periode -- et
certains d'entre eux (C8 : facture manquante chez un fournisseur recurrent)
dependent directement de la LARGEUR de cette periode, pas seulement des
dates presentes dans le dossier. Le type de declaration et la periode sont
donc choisis des l'ecran 1 (pas un aperçu annuel par defaut), pour que la
detection porte exactement sur le meme perimetre que la declaration reelle.
Le prorata, lui, n'a pas d'effet sur la detection : il est ici mis a 0 et la
sortie va dans apercu/, jamais dans annexes/, tant que l'ecran 3 n'a pas
confirme le prorata reel.

Lancement : python3 webapp/serveur.py (ouvre le navigateur tout seul).
Arret     : Ctrl+C dans le terminal ou fermeture de ce terminal.
"""
import errno
import glob
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import lancer_campagne as lc  # noqa: E402  (sys.path modifie juste au-dessus)

STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
HOTE = "127.0.0.1"
PORT = 8743


def _esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


class JournalMemoire:
    """Collecteur en memoire compatible avec l'interface .log() attendue par
    lancer_campagne.traiter_document -- pas de fichier ici, juste renvoye
    tel quel dans la reponse JSON pour affichage dans la page."""

    def __init__(self):
        self.lignes = []

    def log(self, msg=""):
        self.lignes.append(msg)


def choisir_dossier_natif(invite):
    script = (
        'try\n'
        '    set f to choose folder with prompt "%s"\n'
        '    POSIX path of f\n'
        'on error number -128\n'
        '    "ANNULE"\n'
        'end try' % _esc(invite))
    res = lc.osa(script)
    if res is None or res == "ANNULE":
        return None
    return res.rstrip("/")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # le journal du navigateur suffit ; pas de bruit dans le terminal

    # -- utilitaires reponse -------------------------------------------
    def _json(self, data, code=200):
        corps = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _lire_corps_json(self):
        longueur = int(self.headers.get("Content-Length", 0))
        if longueur == 0:
            return {}
        return json.loads(self.rfile.read(longueur).decode("utf-8"))

    def _servir_fichier(self, nom, type_contenu):
        chemin = os.path.join(STATIC_DIR, nom)
        with open(chemin, "rb") as f:
            corps = f.read()
        self.send_response(200)
        self.send_header("Content-Type", type_contenu)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        analyse = urlparse(self.path)
        chemin = analyse.path
        if chemin in ("/", "/index.html"):
            self._servir_fichier("index.html", "text/html; charset=utf-8")
        elif chemin == "/api/societes":
            self._api_societes()
        elif chemin == "/api/pdf":
            self._api_pdf(parse_qs(analyse.query))
        elif chemin == "/api/fichiers":
            self._api_fichiers(parse_qs(analyse.query))
        elif chemin == "/api/telecharger":
            self._api_telecharger(parse_qs(analyse.query))
        else:
            self.send_error(404)

    def do_POST(self):
        chemin = urlparse(self.path).path
        if chemin == "/api/choisir-dossier-factures":
            self._api_choisir_dossier_factures()
        elif chemin == "/api/inventaire":
            self._api_inventaire()
        elif chemin == "/api/lecture":
            self._api_lecture()
        elif chemin == "/api/detection":
            self._api_detection()
        elif chemin == "/api/corriger":
            self._api_corriger()
        elif chemin == "/api/prorata":
            self._api_prorata()
        elif chemin == "/api/replique":
            self._api_replique()
        elif chemin == "/api/xml":
            self._api_xml()
        elif chemin == "/api/generer":
            self._api_generer()
        elif chemin == "/api/ouvrir-dossier":
            self._api_ouvrir_dossier()
        elif chemin == "/api/quitter":
            self._api_quitter()
        else:
            self.send_error(404)

    def _api_societes(self):
        societes = lc.lister_dossiers()
        for s in societes:
            conf = lc.charger_conf(s["chemin"])
            s["source_factures"] = os.path.abspath(os.path.expanduser(
                conf.get("source_factures") or os.path.join(s["chemin"], "factures")))
        self._json({"societes": societes})

    def _api_choisir_dossier_factures(self):
        chemin = choisir_dossier_natif("Choisir le dossier des factures")
        self._json({"chemin": chemin})

    def _api_inventaire(self):
        corps = self._lire_corps_json()
        dossier_nom = corps.get("dossier")
        factures = corps.get("factures") or None
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue : %r" % dossier_nom}, code=400)
            return
        societe = societes[dossier_nom]
        dossier_abs = societe["chemin"]
        cmd = [sys.executable, lc.SCRIPT_ANNEXES, "inventaire", "--dossier", dossier_abs]
        if factures:
            cmd += ["--factures", factures]
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        if r.returncode != 0:
            self._json({"erreur": (r.stderr or r.stdout
                                    or "echec de l'inventaire").strip()}, code=400)
            return
        inv = lc.lire_inventaire(dossier_abs)
        nb_natifs = sum(1 for d in inv["documents"] if d["type"] == "natif")
        nb_scans = len(inv["documents"]) - nb_natifs
        self._json({
            "nb_fichiers": inv["nb_fichiers"],
            "nb_doublons_ecartes": inv["nb_doublons_ecartes"],
            "nb_non_pdf_ignores": inv["nb_non_pdf_ignores"],
            "nb_natifs": nb_natifs,
            "nb_scans": nb_scans,
            "doublons": inv["doublons"],
            "documents": [
                {"fichier": d["fichier"], "sens_chemin": d.get("sens_chemin"),
                 "type": d["type"]}
                for d in inv["documents"]
            ],
        })

    def _api_lecture(self):
        """Lit les factures pas encore extraites, via Claude Code en mode non
        interactif -- meme mecanisme que lancer_campagne.py, reutilise tel
        quel (construction du prompt, validation, ecriture ou mise de cote).
        Sans cette etape, l'ecran de detection n'a rien a montrer : c'est
        elle qui transforme un PDF en donnees exploitables."""
        corps = self._lire_corps_json()
        dossier_nom = corps.get("dossier")
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue : %r" % dossier_nom}, code=400)
            return
        dossier_abs = societes[dossier_nom]["chemin"]
        inv_path = os.path.join(dossier_abs, "extraction", "_inventaire.json")
        if not os.path.isfile(inv_path):
            self._json({"erreur": "Lance d'abord l'inventaire."}, code=400)
            return
        inv = lc.lire_inventaire(dossier_abs)
        a_lire = [d for d in inv["documents"] if not lc.deja_extrait(dossier_abs, d)]
        if not a_lire:
            self._json({"total": 0, "lues": 0, "a_verifier": 0, "journal": []})
            return
        # Version silencieuse : depuis un serveur HTTP, un selecteur de
        # fichier AppleScript surgirait sur le bureau et bloquerait la
        # requete jusqu'au clic. On renvoie la cause a la page a la place.
        commande_claude = lc.trouver_commande_claude()
        if not commande_claude:
            self._json({"erreur": lc.MESSAGE_CLAUDE_ABSENT,
                         "a_lire": len(a_lire)}, code=400)
            return
        conf = lc.charger_conf(dossier_abs)
        referentiel_txt = lc.referentiel_en_texte(dossier_abs)
        taux_admis = conf.get("taux_admis", [0.0, 0.03, 0.08, 0.14, 0.17])
        journal = JournalMemoire()
        n_lues = n_a_verifier = 0
        for doc in a_lire:
            journal.log(doc["fichier"])
            if lc.traiter_document(commande_claude, dossier_abs, doc,
                                    referentiel_txt, taux_admis, journal):
                n_lues += 1
            else:
                n_a_verifier += 1
        self._json({"total": len(a_lire), "lues": n_lues,
                     "a_verifier": n_a_verifier, "journal": journal.lignes})

    # -- ecran 2 : detection ----------------------------------------------
    def _api_detection(self):
        corps = self._lire_corps_json()
        dossier_nom = corps.get("dossier")
        periode = corps.get("periode")
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue : %r" % dossier_nom}, code=400)
            return
        if not periode:
            self._json({"erreur": "periode manquante"}, code=400)
            return
        dossier_abs = societes[dossier_nom]["chemin"]
        inv_path = os.path.join(dossier_abs, "extraction", "_inventaire.json")
        if not os.path.isfile(inv_path):
            self._json({"erreur": "Lance d'abord l'inventaire (ecran 1)."}, code=400)
            return
        inv = lc.lire_inventaire(dossier_abs)

        declare, exceptions, erreur = executer_apercu(dossier_abs, periode)
        if erreur:
            self._json({"erreur": erreur}, code=400)
            return

        # Les revues (ex. C9) n'excluent pas la ligne du controle -- elle
        # reste dans declare["lignes"] avec un simple signalement a cote.
        # On l'accroche donc a la ligne existante plutot que d'en afficher
        # une seconde, dupliquee. Seules les bloquantes (qui EXCLUENT
        # toujours le document) donnent une ligne a part.
        lignes = []
        par_fichier_lignes = {}
        for l in declare["lignes"]:
            l = dict(l)
            l.update({"exception": False, "code": None, "gravite": None, "message": None})
            l["editable"] = fichier_extraction_existe(dossier_abs, l.get("sha256"))
            lignes.append(l)
            par_fichier_lignes.setdefault(l.get("fichier"), []).append(l)

        for e in exceptions:
            fusionnee = False
            if e.get("gravite") == "revue" and e.get("fichier") and e.get("ligne"):
                groupe = par_fichier_lignes.get(e["fichier"]) or []
                idx = e["ligne"] - 1
                if 0 <= idx < len(groupe):
                    cible = groupe[idx]
                    cible.update({"exception": True, "code": e.get("code"),
                                  "gravite": e.get("gravite"), "message": e.get("message")})
                    fusionnee = True
            if not fusionnee:
                lignes.append(ligne_depuis_exception(dossier_abs, inv, e))

        self._json({
            "periode": periode,
            "periode_libelle": declare.get("periode_libelle"),
            "lignes": lignes,
            "hors_periode": declare.get("hors_periode", []),
            "exceptions_bloquantes": declare.get("exceptions_bloquantes", 0),
        })

    # -- ecran 3 : compilation ---------------------------------------------
    def _api_prorata(self):
        corps = self._lire_corps_json()
        dossier_nom = corps.get("dossier")
        periode = corps.get("periode")
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue : %r" % dossier_nom}, code=400)
            return
        if not periode:
            self._json({"erreur": "periode manquante"}, code=400)
            return
        dossier_abs = societes[dossier_nom]["chemin"]
        declare, _exceptions, erreur = executer_apercu(dossier_abs, periode)
        if erreur:
            self._json({"erreur": erreur}, code=400)
            return
        self._json({
            "periode_libelle": declare.get("periode_libelle"),
            "ca_total": declare.get("ca_annee_total"),
            "ca_ouvrant_droit": declare.get("ca_annee_ouvrant_droit"),
            "autres_periodes": declare.get("autres_periodes_declarees"),
            "prorata_brut": declare.get("prorata_brut"),
            "prorata_propose_pct": declare.get("prorata_propose_pct"),
            "exceptions_bloquantes": declare.get("exceptions_bloquantes", 0),
        })

    def _api_generer(self):
        corps = self._lire_corps_json()
        dossier_nom = corps.get("dossier")
        periode = corps.get("periode")
        prorata = corps.get("prorata")
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue : %r" % dossier_nom}, code=400)
            return
        if not periode or prorata in (None, ""):
            self._json({"erreur": "periode ou prorata manquant"}, code=400)
            return
        dossier_abs = societes[dossier_nom]["chemin"]
        dossier_sortie = dossier_sortie_officiel(dossier_abs)
        cmd = [sys.executable, lc.SCRIPT_ANNEXES, "annexes", "--dossier", dossier_abs,
               "--periode", periode, "--prorata", str(prorata), "--sortie", dossier_sortie]
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        if r.returncode != 0:
            self._json({"erreur": (r.stderr or r.stdout
                                    or "echec de la generation").strip()}, code=400)
            return
        declare = lc.dernier_declare(dossier_sortie)
        if declare is None:
            self._json({"erreur": "generation terminee mais instantane introuvable"}, code=500)
            return
        self._json({
            "lignes": len(declare["lignes"]),
            "exceptions_bloquantes": declare.get("exceptions_bloquantes", 0),
            "tva_amont": declare["totaux"]["tva_amont"],
            "tva_deductible": declare["totaux"]["tva_deductible"],
            "fichiers": fichiers_produits(dossier_sortie),
        })

    # -- ecran 3 : livrables derives de l'instantane -----------------------
    #
    # Les deux endpoints qui suivent ne calculent rien : ils rechargent le
    # dernier -DECLARE.json produit par le moteur et le mettent en forme.
    # Les modules correspondants sont importes ici, pas en tete de fichier :
    # ils tirent reportlab / xmlschema, absents d'une installation Python
    # nue, et le reste de la page doit continuer de fonctionner sans eux.

    def _societe_et_sortie(self, corps):
        """Resout dossier -> (chemin absolu, dossier de sortie officiel).
        Renvoie (None, None) apres avoir deja repondu en erreur."""
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        dossier_nom = corps.get("dossier")
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue : %r" % dossier_nom}, code=400)
            return None, None
        dossier_abs = societes[dossier_nom]["chemin"]
        return dossier_abs, dossier_sortie_officiel(dossier_abs)

    def _api_replique(self):
        corps = self._lire_corps_json()
        dossier_abs, dossier_sortie = self._societe_et_sortie(corps)
        if dossier_abs is None:
            return
        try:
            import replique_ecdf
        except ImportError as e:
            self._json({"erreur": "La replique PDF a besoin de reportlab : "
                                   "python3 -m pip install --user reportlab "
                                   "(%s)" % e}, code=400)
            return
        try:
            chemin, infos = replique_ecdf.generer_replique(dossier_abs, dossier_sortie)
        except FileNotFoundError as e:
            self._json({"erreur": "Genere d'abord les annexes : %s" % e}, code=400)
            return
        except Exception as e:
            self._json({"erreur": "Replique impossible : %s" % e}, code=500)
            return
        infos["fichier"] = os.path.basename(chemin)
        self._json(infos)

    def _api_xml(self):
        corps = self._lire_corps_json()
        dossier_abs, dossier_sortie = self._societe_et_sortie(corps)
        if dossier_abs is None:
            return
        try:
            import ecdf_xml
        except ImportError as e:
            self._json({"erreur": "Le XML eCDF a besoin de xmlschema : "
                                   "python3 -m pip install --user xmlschema "
                                   "(%s)" % e}, code=400)
            return
        try:
            chemin, infos = ecdf_xml.generer_xml(dossier_abs, dossier_sortie)
        except ecdf_xml.ControleEchoue as e:
            # Regle 4 du depot : une exception bloquante est remontee telle
            # quelle, jamais contournee. Aucun fichier n'a ete laisse sur le
            # disque par generer_xml dans ce cas.
            self._json({"erreur": "Controle eCDF echoue -- aucun fichier "
                                   "produit.\n%s" % e, "controle": True}, code=400)
            return
        except FileNotFoundError as e:
            self._json({"erreur": "Genere d'abord les annexes : %s" % e}, code=400)
            return
        except Exception as e:
            self._json({"erreur": "XML impossible : %s" % e}, code=500)
            return
        infos["fichier"] = os.path.basename(chemin)
        self._json(infos)

    def _api_fichiers(self, query):
        """Relit la liste des livrables presents. Sert a rafraichir l'ecran 3
        apres production d'un livrable derive, sans rejouer la generation."""
        dossier_nom = (query.get("dossier") or [None])[0]
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue"}, code=400)
            return
        sortie = dossier_sortie_officiel(societes[dossier_nom]["chemin"])
        self._json({"fichiers": fichiers_produits(sortie)})

    def _api_telecharger(self, query):
        dossier_nom = (query.get("dossier") or [None])[0]
        nom = (query.get("fichier") or [None])[0]
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes or not nom or "/" in nom or "\\" in nom:
            self.send_error(400)
            return
        dossier_sortie = dossier_sortie_officiel(societes[dossier_nom]["chemin"])
        chemin = os.path.join(dossier_sortie, nom)
        if (not os.path.isfile(chemin)
                or os.path.dirname(os.path.abspath(chemin)) != dossier_sortie):
            self.send_error(404)
            return
        types = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".txt": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8",
                 # Le PDF s'affiche dans le cadre d'apercu de l'ecran 3 ; le
                 # XML est servi en text/plain pour etre lisible tel quel
                 # dans le navigateur plutot que rendu comme un arbre.
                 ".pdf": "application/pdf", ".xml": "text/plain; charset=utf-8"}
        _, ext = os.path.splitext(nom)
        with open(chemin, "rb") as f:
            corps = f.read()
        self.send_response(200)
        self.send_header("Content-Type", types.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _api_ouvrir_dossier(self):
        corps = self._lire_corps_json()
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        societe = societes.get(corps.get("dossier"))
        if not societe:
            self._json({"erreur": "societe inconnue"}, code=400)
            return
        try:
            subprocess.run(["open", dossier_sortie_officiel(societe["chemin"])])
        except Exception:
            pass
        self._json({"ok": True})

    def _api_quitter(self):
        # Lancee via l'app, cette page n'a ni fenetre ni terminal a fermer --
        # c'est le seul bouton d'arret qui lui reste. La reponse part avant
        # que le process ne s'arrete, sur un thread separe pour laisser le
        # temps au corps HTTP de partir.
        self._json({"ok": True})
        threading.Thread(target=self._arreter_apres_reponse, daemon=True).start()

    @staticmethod
    def _arreter_apres_reponse():
        time.sleep(0.3)
        os._exit(0)

    def _api_pdf(self, query):
        dossier_nom = (query.get("dossier") or [None])[0]
        sha = (query.get("sha") or [None])[0]
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes or not sha:
            self.send_error(400)
            return
        dossier_abs = societes[dossier_nom]["chemin"]
        inv_path = os.path.join(dossier_abs, "extraction", "_inventaire.json")
        if not os.path.isfile(inv_path):
            self.send_error(404)
            return
        inv = lc.lire_inventaire(dossier_abs)
        doc = next((d for d in inv["documents"] if d["sha256"] == sha), None)
        if not doc or not os.path.isfile(doc["chemin"]):
            self.send_error(404)
            return
        with open(doc["chemin"], "rb") as f:
            corps = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _api_corriger(self):
        corps = self._lire_corps_json()
        dossier_nom = corps.get("dossier")
        sha = corps.get("sha") or ""
        champ = corps.get("champ")
        valeur = corps.get("valeur")
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes:
            self._json({"erreur": "societe inconnue"}, code=400)
            return
        if champ not in CHAMPS_CORRIGIBLES:
            self._json({"erreur": "champ non corrigible : %r" % champ}, code=400)
            return
        dossier_abs = societes[dossier_nom]["chemin"]
        p = os.path.join(dossier_abs, "extraction", sha[:12] + ".json") if sha else ""
        if not sha or not os.path.isfile(p):
            self._json({"erreur": "extraction introuvable pour cette facture"}, code=404)
            return
        doc = json.load(open(p, encoding="utf-8"))
        doc[champ] = valeur
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        self._json({"ok": True})


# Champs de l'extraction qu'on autorise a corriger depuis le tableau -- des
# scalaires simples ; les lignes de taux/base/tva ne sont pas couvertes par
# ce premier passage de l'ecran 2.
CHAMPS_CORRIGIBLES = ("date", "tiers", "num_facture", "sens", "regime",
                      "emetteur", "destinataire", "devise", "total_ttc")


def fichier_extraction_existe(dossier_abs, sha):
    if not sha:
        return False
    return os.path.isfile(os.path.join(dossier_abs, "extraction", sha[:12] + ".json"))


def dernieres_exceptions_json(dossier_sortie):
    fichiers = sorted(glob.glob(os.path.join(dossier_sortie, "*-EXCEPTIONS.json")),
                       key=os.path.getmtime, reverse=True)
    if not fichiers:
        return []
    with open(fichiers[0], encoding="utf-8") as f:
        return json.load(f)


def dossier_sortie_officiel(dossier_abs):
    conf = lc.charger_conf(dossier_abs)
    return os.path.abspath(os.path.expanduser(
        conf.get("dossier_sortie") or os.path.join(dossier_abs, "annexes")))


def executer_apercu(dossier_abs, periode, prorata="0"):
    """Lance annexes_tva.py annexes en mode apercu : sortie dans apercu/,
    jamais dans annexes/, tant que l'ecran 3 n'a pas confirme le prorata reel.
    Renvoie (declare, exceptions, erreur) -- erreur est None si tout est bon."""
    apercu = os.path.join(dossier_abs, "apercu")
    os.makedirs(apercu, exist_ok=True)
    cmd = [sys.executable, lc.SCRIPT_ANNEXES, "annexes", "--dossier", dossier_abs,
           "--periode", periode, "--prorata", str(prorata), "--sortie", apercu]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        return None, None, (r.stderr or r.stdout or "echec de la detection").strip()
    declare = lc.dernier_declare(apercu)
    if declare is None:
        return None, None, "aperçu genere mais instantane introuvable"
    return declare, dernieres_exceptions_json(apercu), None


def fichiers_produits(dossier_sortie):
    """Les 4 livrables officiels, retrouves via le -DECLARE.json le plus
    recent (meme base_nom pour les 4)."""
    plus_recent = sorted(glob.glob(os.path.join(dossier_sortie, "*-DECLARE.json")),
                          key=os.path.getmtime, reverse=True)
    if not plus_recent:
        return []
    base = os.path.basename(plus_recent[0])[:-len("-DECLARE.json")]
    fichiers = []
    for suffixe, libelle in ((".xlsx", "Classeur des annexes"),
                              ("-EXCEPTIONS.txt", "Rapport d'exceptions"),
                              ("-CASES-ECDF.txt", "Correspondance eCDF"),
                              ("-DECLARE.json", "Instantané machine")):
        nom = base + suffixe
        if os.path.isfile(os.path.join(dossier_sortie, nom)):
            fichiers.append({"nom": nom, "libelle": libelle})
    # Livrables derives : produits a la demande depuis l'ecran 3, et donc
    # pas toujours presents. Leur nom ne partage pas la base des quatre
    # precedents -- la replique porte sa propre date, le XML porte la
    # reference imposee par eCDF -- d'ou la recherche par motif.
    for motif, libelle, apercu in (("*-REPLIQUE.pdf", "Réplique client (PDF)", "pdf"),
                                    ("*.xml", "Fichier eCDF déposable (XML)", "texte")):
        trouves = sorted(glob.glob(os.path.join(dossier_sortie, motif)),
                          key=os.path.getmtime, reverse=True)
        if trouves:
            fichiers.append({"nom": os.path.basename(trouves[0]),
                              "libelle": libelle, "apercu": apercu})
    return fichiers


def ligne_depuis_exception(dossier_abs, inv, e):
    """Reconstitue une ligne de tableau pour une exception : la valeur brute
    lue (quand l'extraction existe) plus le code/message qui l'a rejetee."""
    par_fichier = {d["fichier"]: d for d in inv["documents"]}
    d = par_fichier.get(e.get("fichier"))
    sha = d["sha256"] if d else None
    base = {}
    if sha:
        p = os.path.join(dossier_abs, "extraction", sha[:12] + ".json")
        if os.path.isfile(p):
            try:
                base = json.load(open(p, encoding="utf-8"))
            except Exception:
                base = {}
    ligne_num, taux, montant_base, tva = e.get("ligne"), None, None, None
    lignes_src = base.get("lignes") or []
    if ligne_num and 1 <= ligne_num <= len(lignes_src):
        l_src = lignes_src[ligne_num - 1]
        taux, montant_base, tva = l_src.get("taux"), l_src.get("base"), l_src.get("tva")
    return {
        "date": base.get("date"), "tiers": e.get("tiers") or base.get("tiers"),
        "num_facture": e.get("num_facture") or base.get("num_facture"),
        "sens": base.get("sens"), "sens_origine": None, "regime": base.get("regime"),
        "base": montant_base, "taux": taux, "tva": tva,
        "fichier": e.get("fichier"), "sha256": sha,
        "editable": fichier_extraction_existe(dossier_abs, sha),
        "exception": True, "code": e.get("code"), "gravite": e.get("gravite"),
        "message": e.get("message"),
    }


def main():
    if not lc.ensure_dependencies():
        return
    url = "http://%s:%d/" % (HOTE, PORT)
    try:
        serveur = ThreadingHTTPServer((HOTE, PORT), Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            # Une instance tourne deja (lancee via l'app, elle n'a ni fenetre
            # ni terminal a fermer -- cf. le lien "Fermer l'application" dans
            # la page). On rouvre simplement dessus plutot que de planter en
            # silence, ce qui serait invisible sans terminal attache.
            print("Un serveur tourne deja sur %s -- ouverture du navigateur." % url)
            webbrowser.open(url)
            return
        raise
    print("Campagne TVA Web -- version 0.1")
    print("Serveur local demarre sur %s" % url)
    print("Accessible uniquement depuis cette machine (127.0.0.1).")
    print("Pour arreter : Ctrl+C ici, fermer ce terminal, ou le lien "
          "'Fermer l'application' dans la page.")
    webbrowser.open(url)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
        print("Serveur arrete.")


if __name__ == "__main__":
    main()
