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
import datetime as dt
import glob
import hashlib
import io
import json
import os
import re
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
        elif chemin == "/api/apercu-xlsx":
            self._api_apercu_xlsx(parse_qs(analyse.query))
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
        elif chemin == "/api/declaration":
            self._api_declaration()
        elif chemin == "/api/generer":
            self._api_generer()
        elif chemin == "/api/ouvrir-dossier":
            self._api_ouvrir_dossier()
        elif chemin == "/api/quitter":
            self._api_quitter()
        elif chemin == "/api/societe":
            self._api_creer_societe()
        elif chemin == "/api/transaction":
            self._api_ajouter_transaction()
        else:
            self.send_error(404)

    def _api_societes(self):
        societes = lc.lister_dossiers()
        for s in societes:
            conf = lc.charger_conf(s["chemin"])
            s["source_factures"] = os.path.abspath(os.path.expanduser(
                conf.get("source_factures") or os.path.join(s["chemin"], "factures")))
            # Etat de completude vis-a-vis d'eCDF, calcule ici pour que la
            # liste deroulante puisse signaler une societe non deposable
            # AVANT qu'on lance une campagne entiere pour rien.
            s.update(etat_ecdf(conf))
            # Alimente les listes de la fenetre de saisie manuelle : les
            # valeurs admises viennent du dossier, jamais codees dans la page.
            s["taux_admis"] = conf.get("taux_admis") or [0.0, 0.03, 0.08, 0.14, 0.17]
        self._json({"societes": societes, "regimes_admis": lc.REGIMES_ADMIS})

    def _api_creer_societe(self):
        corps = self._lire_corps_json()
        valeurs, erreurs = valider_societe(corps)
        if erreurs:
            self._json({"erreurs": erreurs}, code=400)
            return
        cible = os.path.join(lc.DOSSIERS, valeurs["code"])
        if os.path.exists(cible):
            self._json({"erreurs": {"code": "Un dossier « %s » existe déjà."
                                     % valeurs["code"]}}, code=400)
            return
        try:
            chemin = ecrire_societe_yaml(cible, valeurs)
        except Exception as e:
            self._json({"erreurs": {"_": "Écriture impossible : %s" % e}}, code=500)
            return
        self._json({"ok": True, "dossier": valeurs["code"], "fichier": chemin})

    def _api_ajouter_transaction(self):
        corps = self._lire_corps_json()
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        dossier_nom = corps.get("dossier")
        if dossier_nom not in societes:
            self._json({"erreurs": {"_": "societe inconnue"}}, code=400)
            return
        dossier_abs = societes[dossier_nom]["chemin"]
        ext = _dossier_extraction(dossier_abs)
        if not os.path.isfile(os.path.join(ext, "_inventaire.json")):
            self._json({"erreurs": {"_": "Lance d'abord l'inventaire : la saisie "
                                          "vient s'ajouter aux factures inventoriées."}},
                        code=400)
            return
        conf = lc.charger_conf(dossier_abs)
        document, entree, erreurs = valider_transaction(corps, conf)
        if erreurs:
            self._json({"erreurs": erreurs}, code=400)
            return

        manuelles = lire_manuelles(dossier_abs)
        if any(m["sha256"] == document["sha256"] for m in manuelles):
            self._json({"erreurs": {"_": "Cette transaction a déjà été saisie "
                                          "(mêmes tiers, numéro, date et montants)."}},
                        code=400)
            return
        try:
            _ecrire_json(os.path.join(ext, entree["extraction"]), document)
            manuelles.append({"sha256": document["sha256"],
                               "entree_inventaire": entree,
                               "saisie_le": document["saisie_le"]})
            _ecrire_json(os.path.join(ext, FICHIER_MANUELLES), manuelles)
            injecter_manuelles(dossier_abs)
        except Exception as e:
            self._json({"erreurs": {"_": "Écriture impossible : %s" % e}}, code=500)
            return
        self._json({"ok": True, "libelle": document["fichier"],
                     "total_manuelles": len(manuelles)})

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
        # etape_inventaire reecrit _inventaire.json en entier : les
        # transactions saisies a la main en disparaitraient. On les remet.
        nb_manuelles = injecter_manuelles(dossier_abs)
        inv = lc.lire_inventaire(dossier_abs)
        nb_natifs = sum(1 for d in inv["documents"] if d["type"] == "natif")
        nb_manuel = sum(1 for d in inv["documents"] if d["type"] == "manuel")
        nb_scans = len(inv["documents"]) - nb_natifs - nb_manuel
        self._json({
            "nb_fichiers": inv["nb_fichiers"],
            "nb_doublons_ecartes": inv["nb_doublons_ecartes"],
            "nb_non_pdf_ignores": inv["nb_non_pdf_ignores"],
            "nb_natifs": nb_natifs,
            "nb_scans": nb_scans,
            "nb_manuelles": nb_manuel,
            "manuelles_reinjectees": nb_manuelles,
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
        dossier_sortie = dossier_sortie_officiel(dossier_abs, periode)
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
        periode = corps.get("periode")
        if not periode:
            self._json({"erreur": "periode manquante"}, code=400)
            return None, None
        return dossier_abs, dossier_sortie_officiel(dossier_abs, periode)

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

    def _api_declaration(self):
        corps = self._lire_corps_json()
        dossier_abs, dossier_sortie = self._societe_et_sortie(corps)
        if dossier_abs is None:
            return
        try:
            import declaration_officielle
        except ImportError as e:
            self._json({"erreur": "Ce livrable a besoin de PyMuPDF et reportlab : "
                                   "python3 -m pip install --user pymupdf reportlab "
                                   "(%s)" % e}, code=400)
            return
        try:
            chemin, infos = declaration_officielle.generer(dossier_abs, dossier_sortie)
        except FileNotFoundError as e:
            self._json({"erreur": "%s" % e}, code=400)
            return
        except Exception as e:
            self._json({"erreur": "Document impossible : %s" % e}, code=500)
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
        periode = (query.get("periode") or [None])[0]
        if not periode:
            self._json({"erreur": "periode manquante"}, code=400)
            return
        sortie = dossier_sortie_officiel(societes[dossier_nom]["chemin"], periode)
        self._json({"fichiers": fichiers_produits(sortie)})

    def _api_apercu_xlsx(self, query):
        """Rend un classeur en HTML, onglet par onglet.

        Le cadre d'apercu de l'ecran 3 est une iframe : elle sait afficher du
        PDF, du texte et du HTML, mais pas un .xlsx. Plutot que de renvoyer
        l'utilisateur vers Excel pour un simple coup d'oeil, on relit le
        classeur produit et on le transcrit en tableaux. Aucune valeur n'est
        recalculee : openpyxl est ouvert en data_only, donc on lit les
        resultats deja ecrits par le moteur, jamais les formules.
        """
        dossier_nom = (query.get("dossier") or [None])[0]
        periode = (query.get("periode") or [None])[0]
        nom = (query.get("fichier") or [None])[0]
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if (dossier_nom not in societes or not periode or not nom
                or "/" in nom or "\\" in nom or not nom.lower().endswith(".xlsx")):
            self.send_error(400)
            return
        dossier_sortie = dossier_sortie_officiel(societes[dossier_nom]["chemin"], periode)
        chemin = os.path.join(dossier_sortie, nom)
        if (not os.path.isfile(chemin)
                or os.path.dirname(os.path.abspath(chemin)) != dossier_sortie):
            self.send_error(404)
            return
        try:
            html = classeur_en_html(chemin)
        except Exception as e:
            html = ("<p style='color:#b42318;font:14px -apple-system,sans-serif;"
                    "padding:24px'>Aperçu impossible : %s</p>" % _echapper(str(e)))
        corps = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _api_telecharger(self, query):
        dossier_nom = (query.get("dossier") or [None])[0]
        nom = (query.get("fichier") or [None])[0]
        societes = {s["dossier"]: s for s in lc.lister_dossiers()}
        if dossier_nom not in societes or not nom or "/" in nom or "\\" in nom:
            self.send_error(400)
            return
        periode = (query.get("periode") or [None])[0]
        if not periode:
            self.send_error(400)
            return
        dossier_sortie = dossier_sortie_officiel(societes[dossier_nom]["chemin"], periode)
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
            subprocess.run(["open", dossier_sortie_officiel(
                societe["chemin"], corps.get("periode"))])
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


# =====================================================================
# Fiche societe : ce qu'eCDF exige pour qu'une declaration soit deposable
#
# Le bloc <Declarer> du XML porte exactement trois identifiants, et le
# schema en fixe la forme :
#   MatrNbr  11 ou 13 chiffres          (matricule national)
#   RCSNbr   une lettre + 6 alphanum.   (ex. B123456) ou "NE" si absent
#   VATNbr   8 chiffres SANS le prefixe LU, ou "NE" si absent
# Le reste (type de formulaire, annee, periode) se deduit de la campagne.
#
# Les regles de forme ne sont PAS reecrites ici : on appelle les memes
# fonctions que le generateur XML, pour qu'un formulaire accepte ne
# puisse pas produire un fichier rejete plus tard.
# =====================================================================

def _normalisateurs():
    """Renvoie les trois normalisateurs d'ecdf_xml, ou None si le module
    n'est pas importable (xmlschema absent). La fiche societe reste alors
    saisissable : c'est la generation du XML qui refusera, plus tard."""
    try:
        import ecdf_xml
        return (ecdf_xml.normaliser_matricule, ecdf_xml.normaliser_rcs,
                ecdf_xml.normaliser_tva)
    except Exception:
        return None


CHAMPS_ECDF = (
    ("matricule", "Matricule", "11 ou 13 chiffres"),
    ("rcs", "N° RCS", "ex. B123456, ou NE"),
    ("numero_tva", "N° de TVA", "8 chiffres sans le préfixe LU, ou NE"),
)


def _renseigne(v):
    """Un champ vide n'est PAS un champ a "NE".

    Les normalisateurs d'ecdf_xml traduisent une valeur absente en "NE",
    ce qui est juste au regard du schema : "NE" est la facon officielle de
    dire "cette societe n'a pas de RCS". Mais pour une FICHE, vide veut
    dire "pas encore saisi" -- et un declarant qui depose une declaration
    de TVA a forcement un numero de TVA. Confondre les deux ferait passer
    une fiche incomplete pour deposable, et deposerait "NE" a la place du
    vrai numero. Seul un "NE" ecrit explicitement vaut renonciation.
    """
    return str(v or "").strip() != ""


def _matricule_factice(v):
    """Le gabarit livre '0000 0000 000' : 11 chiffres, donc accepte par le
    schema, mais ce n'est evidemment pas un matricule."""
    return set(re.sub(r"\D", "", str(v or ""))) <= {"0"}


def etat_ecdf(conf):
    """Dit si la fiche permet un depot, et ce qui manque le cas echeant."""
    n = _normalisateurs()
    if n is None:
        return {"pret_ecdf": None, "manques_ecdf": []}
    norm_matr, norm_rcs, norm_tva = n
    manques = []
    if (not _renseigne(conf.get("matricule"))
            or _matricule_factice(conf.get("matricule"))
            or norm_matr(conf.get("matricule")) is None):
        manques.append("matricule")
    if not _renseigne(conf.get("rcs")) or norm_rcs(conf.get("rcs")) is None:
        manques.append("RCS")
    if not _renseigne(conf.get("numero_tva")) or norm_tva(conf.get("numero_tva")) is None:
        manques.append("n° TVA")
    return {"pret_ecdf": not manques, "manques_ecdf": manques}


def valider_societe(corps):
    """Controle une fiche saisie. Renvoie (valeurs_propres, erreurs)."""
    erreurs = {}
    v = {}

    # Le code sert de nom de dossier : on le contraint durement plutot que
    # d'assainir en silence, pour que le dossier porte bien ce qui a ete
    # saisi. Pas de separateur de chemin, pas d'accent, pas d'espace.
    code = str(corps.get("code") or "").strip().upper()
    if not code:
        erreurs["code"] = "Obligatoire."
    elif not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,39}", code):
        erreurs["code"] = ("2 à 40 caractères : lettres non accentuées, "
                            "chiffres, tiret ou souligné.")
    v["code"] = code

    denomination = str(corps.get("denomination") or "").strip()
    if not denomination:
        erreurs["denomination"] = "Obligatoire."
    v["denomination"] = denomination

    n = _normalisateurs()
    if n is None:
        # Sans les normalisateurs on ne peut pas garantir la forme ; on
        # refuse plutot que d'enregistrer une fiche qui semblera valide.
        erreurs["_"] = ("Contrôles eCDF indisponibles (module ecdf_xml non "
                        "importable) — fiche non enregistrée.")
        return v, erreurs
    norm_matr, norm_rcs, norm_tva = n

    matr = norm_matr(corps.get("matricule"))
    if not _renseigne(corps.get("matricule")):
        erreurs["matricule"] = "Obligatoire — 11 ou 13 chiffres."
    elif _matricule_factice(corps.get("matricule")):
        erreurs["matricule"] = "Un matricule ne peut pas être uniquement des zéros."
    elif matr is None:
        erreurs["matricule"] = "11 ou 13 chiffres attendus."
    v["matricule"] = matr

    rcs = norm_rcs(corps.get("rcs"))
    if not _renseigne(corps.get("rcs")):
        erreurs["rcs"] = ("Obligatoire — saisir NE si la société n'est pas "
                           "immatriculée au RCS.")
    elif rcs is None:
        erreurs["rcs"] = "Format attendu : une lettre puis jusqu'à 6 caractères (ex. B123456), ou NE."
    v["rcs"] = rcs

    tva = norm_tva(corps.get("numero_tva"))
    if not _renseigne(corps.get("numero_tva")):
        erreurs["numero_tva"] = ("Obligatoire — saisir NE seulement si la société "
                                  "n'a pas de numéro de TVA.")
    elif tva is None:
        erreurs["numero_tva"] = "8 chiffres sans le préfixe LU, ou NE."
    v["numero_tva"] = tva

    periodicite = str(corps.get("periodicite") or "").strip().lower()
    if periodicite not in ("mensuelle", "trimestrielle"):
        erreurs["periodicite"] = "Choisir mensuelle ou trimestrielle."
    v["periodicite"] = periodicite

    # Alias : servent a reconnaitre la societe comme emetteur ou
    # destinataire d'une facture quand le dossier source ne separe pas
    # achats et ventes. La denomination est ajoutee d'office.
    alias = corps.get("alias_societe") or []
    if isinstance(alias, str):
        alias = [a.strip() for a in alias.split(",")]
    alias = [a for a in (str(x).strip() for x in alias) if a]
    if denomination and denomination not in alias:
        alias.insert(0, denomination)
    v["alias_societe"] = alias

    source = str(corps.get("source_factures") or "").strip()
    v["source_factures"] = source or None
    if source and not os.path.isdir(os.path.expanduser(source)):
        erreurs["source_factures"] = "Dossier introuvable : %s" % source

    return v, erreurs


def ecrire_societe_yaml(cible, v):
    """Ecrit dossiers/<CODE>/societe.yaml a partir du gabarit versionne.

    Le gabarit est la reference : on n'en reconstruit pas une copie ici,
    on y substitue les valeurs saisies. Tout ce que le gabarit documente
    (exigibilite, prorata, taux admis, onglets de sortie) est ainsi
    conserve, commentaires compris.
    """
    gabarit = os.path.join(REPO, "config", "societe.template.yaml")
    with io.open(gabarit, encoding="utf-8") as f:
        texte = f.read()

    def remplacer(cle, valeur, motif=None):
        nonlocal texte
        motif = motif or r"^%s:.*$" % re.escape(cle)
        texte = re.sub(motif, "%s: %s" % (cle, valeur), texte,
                        count=1, flags=re.MULTILINE)

    remplacer("code", v["code"])
    remplacer("denomination", json.dumps(v["denomination"], ensure_ascii=False))
    remplacer("numero_tva", '"%s"' % v["numero_tva"])
    remplacer("matricule", '"%s"' % v["matricule"])
    remplacer("alias_societe",
              "[%s]" % ", ".join(json.dumps(a, ensure_ascii=False)
                                  for a in v["alias_societe"]))
    remplacer("source_factures",
              json.dumps(v["source_factures"], ensure_ascii=False)
              if v["source_factures"] else "null")
    remplacer("periodicite", v["periodicite"])

    # Le RCS n'existe pas dans le gabarit historique : le generateur XML le
    # lit pourtant (conf_societe["rcs"]). On l'insere juste apres le
    # matricule, la ou il se lit naturellement.
    if not re.search(r"^rcs:", texte, flags=re.MULTILINE):
        texte = re.sub(r"^(matricule:.*)$",
                        r'\1\nrcs: "%s"                     '
                        r'# RCS du declarant, ou NE' % v["rcs"],
                        texte, count=1, flags=re.MULTILINE)
    else:
        remplacer("rcs", '"%s"' % v["rcs"])

    os.makedirs(cible, exist_ok=True)
    chemin = os.path.join(cible, "societe.yaml")
    with io.open(chemin, "w", encoding="utf-8") as f:
        f.write(texte)
    return chemin


# =====================================================================
# Transactions saisies a la main
#
# Le moteur construit ses lignes en parcourant _inventaire.json puis en
# chargeant, pour chaque document, son fichier d'extraction. Une transaction
# sans facture peut donc entrer par la meme porte : une entree d'inventaire
# plus un fichier d'extraction de meme forme. Aucun calcul n'est fait ici et
# le moteur n'est pas modifie -- la ligne suit exactement le meme chemin de
# controle qu'une facture lue.
#
# Les saisies vivent AUSSI dans extraction/_manuelles.json : etape_inventaire
# reecrit _inventaire.json en entier a chaque passage, ce qui les effacerait.
# Elles sont donc reinjectees apres chaque inventaire.
# =====================================================================

FICHIER_MANUELLES = "_manuelles.json"


def _dossier_extraction(dossier_abs):
    return os.path.join(dossier_abs, "extraction")


def lire_manuelles(dossier_abs):
    p = os.path.join(_dossier_extraction(dossier_abs), FICHIER_MANUELLES)
    if not os.path.isfile(p):
        return []
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _ecrire_json(chemin, donnees):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with io.open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)


def injecter_manuelles(dossier_abs):
    """Reinjecte les saisies dans _inventaire.json. Idempotent : une saisie
    deja presente n'est pas dupliquee."""
    ext = _dossier_extraction(dossier_abs)
    chemin_inv = os.path.join(ext, "_inventaire.json")
    if not os.path.isfile(chemin_inv):
        return 0
    manuelles = lire_manuelles(dossier_abs)
    if not manuelles:
        return 0
    with io.open(chemin_inv, encoding="utf-8") as f:
        inv = json.load(f)
    connus = {d.get("sha256") for d in inv.get("documents", [])}
    ajoutees = 0
    for m in manuelles:
        if m["sha256"] in connus:
            continue
        inv.setdefault("documents", []).append(m["entree_inventaire"])
        ajoutees += 1
    if ajoutees:
        inv["nb_fichiers"] = len(inv["documents"])
        _ecrire_json(chemin_inv, inv)
    return ajoutees


def valider_transaction(corps, conf):
    """Controle une saisie. Renvoie (document, entree_inventaire, erreurs).

    Les regles sont celles du moteur : regimes admis, taux admis, coherence
    base x taux contre TVA saisie dans la tolerance du dossier. Elles sont
    verifiees ICI pour que l'erreur soit dite au moment de la saisie, mais le
    moteur les revoit ensuite de toute facon -- c'est lui qui fait foi.
    """
    erreurs = {}
    v = {}

    sens = str(corps.get("sens") or "").strip().lower()
    if sens not in ("achat", "vente"):
        erreurs["sens"] = "Choisir achat ou vente."
    v["sens"] = sens

    regime = str(corps.get("regime") or "").strip()
    if regime not in lc.REGIMES_ADMIS:
        erreurs["regime"] = "Régime non admis."
    v["regime"] = regime

    tiers = str(corps.get("tiers") or "").strip()
    if not tiers:
        erreurs["tiers"] = "Obligatoire."
    v["tiers"] = tiers

    date = str(corps.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        erreurs["date"] = "Format attendu : AAAA-MM-JJ."
    v["date"] = date

    v["num_facture"] = str(corps.get("num_facture") or "").strip() or None
    v["pays"] = (str(corps.get("pays") or "").strip().upper() or None)
    v["devise"] = str(corps.get("devise") or "EUR").strip().upper() or "EUR"

    def nombre(cle, obligatoire=True):
        brut = corps.get(cle)
        if brut in (None, ""):
            if obligatoire:
                erreurs[cle] = "Obligatoire."
            return None
        try:
            # Les claviers francais produisent des virgules decimales.
            return float(str(brut).replace(",", ".").replace(" ", ""))
        except ValueError:
            erreurs[cle] = "Nombre attendu."
            return None

    base = nombre("base")
    tva = nombre("tva")
    taux = nombre("taux")
    taux_change = nombre("taux_change", obligatoire=False)
    v["taux_change"] = taux_change if taux_change else 1.0

    taux_admis = conf.get("taux_admis") or [0.0, 0.03, 0.08, 0.14, 0.17]
    if taux is not None and taux not in taux_admis:
        erreurs["taux"] = ("Taux non admis. Admis : %s."
                            % ", ".join(str(x) for x in taux_admis))

    # Meme controle que le moteur (C3) : base x taux doit retrouver la TVA
    # saisie, a la tolerance du dossier pres. Une saisie incoherente serait
    # rejetee plus tard ; autant le dire tout de suite.
    tolerance = conf.get("tolerance_arrondi", 0.02)
    if base is not None and tva is not None and taux is not None:
        ecart = abs(round(base * taux, 2) - round(tva, 2))
        if ecart > tolerance:
            erreurs["tva"] = ("Incohérent : %.2f x %g = %.2f, écart de %.2f "
                               "(tolérance %.2f)."
                               % (base, taux, base * taux, ecart, tolerance))

    if erreurs:
        return None, None, erreurs

    # Identifiant : une empreinte du contenu saisi, pour que deux saisies
    # identiques ne creent pas deux lignes, et pour ressembler a un sha256
    # de facture (meme place, meme role dans l'inventaire).
    empreinte = hashlib.sha256(
        ("MANUEL|%s|%s|%s|%s|%s|%s" % (sens, tiers, v["num_facture"], date, base, tva))
        .encode("utf-8")).hexdigest()

    libelle = "SAISIE MANUELLE — %s%s du %s" % (
        tiers, (" n° %s" % v["num_facture"]) if v["num_facture"] else "", date)

    document = {
        "sens": sens, "tiers": tiers, "tva_tiers": str(corps.get("tva_tiers") or "").strip() or None,
        "pays": v["pays"], "num_facture": v["num_facture"], "date": date,
        "devise": v["devise"], "taux_change": v["taux_change"],
        "total_ttc": round((base or 0) + (tva or 0), 2),
        "regime": regime,
        "source_extraction": "saisie manuelle",
        "saisie_manuelle": True,
        "motif_saisie": str(corps.get("motif") or "").strip() or None,
        "saisie_le": dt.datetime.now().isoformat(timespec="seconds"),
        "lignes": [{"taux": taux, "base": base, "tva": tva,
                     "description": str(corps.get("description") or "").strip() or libelle}],
        "fichier": libelle,
        "sha256": empreinte,
    }
    entree = {
        "sha256": empreinte, "fichier": libelle, "chemin": "",
        # Pas de chemin, donc pas de sens deductible du dossier : c'est la
        # saisie elle-meme qui porte le sens, et elle est explicite.
        "sens_chemin": None, "type": "manuel", "texte": None,
        "extraction": empreinte[:12] + ".json", "statut": "saisi",
    }
    return document, entree, {}


def _echapper(s):
    """Echappement HTML : le contenu vient de factures lues, donc de texte
    dont on ne maitrise pas la forme. Il est affiche, jamais interprete."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def classeur_en_html(chemin):
    """Transcrit un .xlsx en HTML lisible, un tableau par onglet.

    data_only=True : on lit les valeurs deja calculees et ecrites par le
    moteur, pas les formules. Rien n'est recalcule ici.
    """
    import openpyxl
    classeur = openpyxl.load_workbook(chemin, data_only=True, read_only=True)
    morceaux = ["""<style>
      body { font: 13px -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
             margin: 0; padding: 16px; color: #1c1e21; background: #fff; }
      h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
           color: #6b7280; margin: 22px 0 8px; }
      h2:first-of-type { margin-top: 0; }
      table { border-collapse: collapse; width: 100%; margin-bottom: 8px; }
      th, td { border-bottom: 1px solid #e2e4e9; padding: 5px 8px;
               text-align: left; vertical-align: top; white-space: nowrap; }
      th { background: #f5f6f8; color: #6b7280; font-size: 11px;
           text-transform: uppercase; position: sticky; top: 0; }
      td.n { text-align: right; font-variant-numeric: tabular-nums; }
      tr:hover td { background: #f9fafb; }
      .vide { color: #6b7280; font-style: italic; }
    </style>"""]
    for onglet in classeur.worksheets:
        morceaux.append("<h2>%s</h2>" % _echapper(onglet.title))
        lignes = list(onglet.iter_rows(values_only=True))
        # Les lignes entierement vides servent d'aeration dans le classeur ;
        # elles n'apportent rien a un tableau HTML.
        lignes = [l for l in lignes if any(c is not None and str(c).strip() for c in l)]
        if not lignes:
            morceaux.append("<p class='vide'>Onglet vide.</p>")
            continue
        # Pas de <thead> : les onglets commencent par des lignes de titre
        # (societe, periode, intitule), pas par un en-tete de colonnes.
        # Promouvoir la premiere ligne au rang d'en-tete la deformerait.
        morceaux.append("<table><tbody>")
        for l in lignes:
            morceaux.append("<tr>")
            for c in l:
                if c is None:
                    morceaux.append("<td></td>")
                elif isinstance(c, (int, float)):
                    morceaux.append("<td class='n'>%s</td>"
                                     % _echapper(("%.2f" % c) if isinstance(c, float) else c))
                else:
                    morceaux.append("<td>%s</td>" % _echapper(c))
            morceaux.append("</tr>")
        morceaux.append("</tbody></table>")
    classeur.close()
    return "\n".join(morceaux)


def decouper_periode(periode):
    """'2025-Q3' -> ('2025', 'Q3'). Le suffixe est celui du moteur (Q3, M08,
    ANNUAL) : le dossier porte le meme nom que ce que contiennent les
    fichiers qu'il abrite, rien a traduire."""
    return periode[:4], periode[5:]


def dossier_sortie_officiel(dossier_abs, periode=None):
    """Racine des livrables : <dossier client>/<annee>/<periode>.

    Sans periode, renvoie la racine historique -- utile pour les appels qui
    ne ciblent pas une periode precise. Un dossier_sortie force dans
    societe.yaml reste prioritaire : il designe alors la racine, sous
    laquelle l'annee et la periode s'ajoutent de la meme facon.
    """
    conf = lc.charger_conf(dossier_abs)
    racine = os.path.abspath(os.path.expanduser(
        conf.get("dossier_sortie") or dossier_abs))
    if periode is None:
        return racine
    annee, code = decouper_periode(periode)
    return os.path.join(racine, annee, code)


def executer_apercu(dossier_abs, periode, prorata="0"):
    """Lance annexes_tva.py annexes en mode apercu : sortie dans apercu/,
    jamais dans annexes/, tant que l'ecran 3 n'a pas confirme le prorata reel.
    Renvoie (declare, exceptions, erreur) -- erreur est None si tout est bon."""
    # Sous la periode, prefixe '_' : le prorata annuel ecarte ces dossiers,
    # un apercu n'etant pas une declaration.
    apercu = os.path.join(dossier_sortie_officiel(dossier_abs, periode), "_apercu")
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
                                    ("*-DECLARATION-OFFICIELLE-*.pdf",
                                     "Déclaration officielle et annexes (PDF)", "pdf"),
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
