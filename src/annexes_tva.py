#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moteur de production des annexes de declaration TVA periodique (Luxembourg).

PRINCIPE : tout calcul est deterministe et rejouable. Aucune agregation n'est
produite par un modele de langage. L'IA n'intervient qu'a l'etape de LECTURE
des factures, dont le resultat est fige dans des JSON horodates qui
constituent la piste d'audit.

SORTIES : un classeur .xlsx (annexes uniquement) + deux fichiers .txt
          (exceptions, correspondance cases eCDF).

USAGE
  python3 annexes_tva.py inventaire --dossier <chemin>
  python3 annexes_tva.py annexes    --dossier <chemin>
La periode et le prorata sont demandes de facon interactive.
"""
import argparse, datetime as dt, glob, hashlib, json, os, re, subprocess, sys
from collections import defaultdict

try:
    import yaml
except ImportError:
    sys.exit("PyYAML requis : pip3 install pyyaml")
try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("openpyxl requis : pip3 install openpyxl")

CENT = 0.005

# Regle d'exigibilite appliquee, non parametrable.
# Regime des debits : rattachement par DATE D'EMISSION de la facture,
# IDENTIQUE en amont (TVA deductible) et en aval (TVA collectee).
#   - fondement : loi TVA du 12.02.1979 modifiee, art. 24 par. 1er, par
#     derogation a l'art. 21 ;
#   - l'option pour l'imposition d'apres les recettes (art. 25 par. 1er) n'est
#     exercee par aucune societe du portefeuille ;
#   - regle confirmee par le preparateur pour les deux sens, comptabilite
#     tenue en regime debits.
EXIGIBILITE = "debits"

# Regimes ou la TVA n'est PAS facturee par le fournisseur mais autoliquidee par
# le preneur. Le total de la facture y est egal a la base : le controle C2 en
# tient compte.
AUTOLIQUIDATION = ("acq_intracom", "service_autoliq", "service_autoliq_tiers", "import")
MOIS_EN = ["", "January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

# ----------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------
def r2(x):
    if x is None:
        return None
    return float(int(abs(x) * 100 + 0.5 + 1e-9)) / 100 * (1 if x >= 0 else -1)

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()

def charger_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def bornes_periode(periode):
    """'2025-Q3' | '2025-M08' | '2025-ANNUAL' -> (debut, fin, libelle affiche)."""
    an = int(periode[:4]); suf = periode[5:]
    if suf.startswith("Q"):
        q = int(suf[1:]); m0 = 3 * (q - 1) + 1; m1 = m0 + 2
        libelle = "Quarter %d %d" % (q, an)
    elif suf.startswith("M"):
        m0 = m1 = int(suf[1:]); libelle = "%s %d" % (MOIS_EN[m0], an)
    elif suf == "ANNUAL":
        m0, m1 = 1, 12; libelle = "Annual %d" % an
    else:
        raise ValueError("Periode invalide : %s" % periode)
    debut = dt.date(an, m0, 1)
    fin = dt.date(an + (m1 == 12), (m1 % 12) + 1, 1) - dt.timedelta(days=1)
    return debut, fin, libelle

# ----------------------------------------------------------------------
# Saisie interactive
# ----------------------------------------------------------------------
def _lire(invite, defaut=None, valide=None):
    while True:
        suf = " [%s]" % defaut if defaut is not None else ""
        rep = input("  %s%s : " % (invite, suf)).strip()
        if not rep and defaut is not None:
            rep = str(defaut)
        if valide is None or valide(rep):
            return rep
        print("    valeur invalide")

def demander_periode(defaut_periodicite=None):
    print("\nPERIODE DE DECLARATION")
    print("  1) Mensuelle    2) Trimestrielle    3) Annuelle")
    d = {"mensuelle": "1", "trimestrielle": "2", "annuelle": "3"}.get(defaut_periodicite)
    c = _lire("Type de declaration", d, lambda x: x in ("1", "2", "3"))
    an = int(_lire("Annee", dt.date.today().year, lambda x: re.fullmatch(r"\d{4}", x)))
    if c == "1":
        m = int(_lire("Mois (1-12)", None, lambda x: x.isdigit() and 1 <= int(x) <= 12))
        return "%d-M%02d" % (an, m)
    if c == "2":
        q = int(_lire("Trimestre (1-4)", None, lambda x: x.isdigit() and 1 <= int(x) <= 4))
        return "%d-Q%d" % (an, q)
    return "%d-ANNUAL" % an

def ca_de_l_annee(dossier_sortie, annee, valides, periode):
    """Chiffre d'affaires de l'ANNEE CIVILE : lignes de vente de la periode en
    cours, completees par celles des autres periodes deja declarees de la meme
    annee (instantanes -DECLARE.json). Dedoublonnage par facture et par taux."""
    lignes, vus = [], set()
    def ajouter(l):
        cle = ((l.get("tiers") or "").lower(), (l.get("num_facture") or ""),
               l.get("date"), l.get("taux"))
        if l.get("sens") == "vente" and cle not in vus:
            vus.add(cle); lignes.append(l)
    for l in valides:
        ajouter(l)
    autres = 0
    if os.path.isdir(dossier_sortie):
        for f in sorted(glob.glob(os.path.join(dossier_sortie, "*-DECLARE.json"))):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            if not d.get("periode", "").startswith(str(annee)) or d["periode"] == periode:
                continue
            autres += 1
            for l in d.get("lignes", []):
                ajouter(l)
    total = r2(sum(l["base"] for l in lignes))
    droit = r2(sum(l["base"] for l in lignes if l["regime"] in REGIMES_CA_DROIT))
    return total, droit, autres

def prorata_art50(ca_droit, ca_total):
    """Prorata general de deduction : CA ouvrant droit / CA total, arrondi a
    l'unite superieure (directive 2006/112/CE art. 175 par. 1 ; transpose a
    l'art. 50 de la loi TVA luxembourgeoise)."""
    if not ca_total:
        return None, None
    brut = ca_droit / ca_total
    return brut, int(brut * 100 + 0.999999) / 100.0

def demander_prorata(conf, ca_total, ca_droit, annee, autres_periodes):
    """Le prorata est CALCULE sur le chiffre d'affaires de l'annee civile.
    La valeur calculee est proposee ; elle peut etre remplacee, auquel cas
    l'origine de la saisie est tracee sur le livrable."""
    print("\nDROIT A DEDUCTION - prorata calcule sur le CA de l'annee %s" % annee)
    brut, arrondi = prorata_art50(ca_droit, ca_total)
    print("  CA total                 : %12.2f" % ca_total)
    print("  CA ouvrant droit         : %12.2f" % ca_droit)
    if autres_periodes:
        print("  (dont %d autre(s) periode(s) declaree(s) de l'exercice)" % autres_periodes)
    if brut is None:
        print("  Aucun chiffre d'affaires sur l'annee : le prorata ne peut pas etre calcule.")
        dd = conf.get("droit_deduction") or {}
        pct = _lire("Prorata a appliquer en %", dd.get("prorata_pourcent"),
                    lambda x: re.fullmatch(r"\d{1,3}([.,]\d+)?", x)
                    and float(x.replace(",", ".")) <= 100)
        ex = _lire("Origine (exercice de reference)", dd.get("exercice_reference"),
                   lambda x: bool(x))
        return float(pct.replace(",", ".")), "saisi - %s" % ex, None
    print("  Prorata                  : %.6f -> %g %% (arrondi a l'unite superieure)"
          % (brut, arrondi * 100))
    rep = _lire("Prorata retenu en %", "%g" % (arrondi * 100),
                lambda x: re.fullmatch(r"\d{1,3}([.,]\d+)?", x)
                and float(x.replace(",", ".")) <= 100)
    pct = float(rep.replace(",", "."))
    origine = ("calcule sur le CA %s" % annee if abs(pct - arrondi * 100) < 1e-9
               else "saisi (calcul : %g %%)" % (arrondi * 100))
    return pct, origine, brut

# ----------------------------------------------------------------------
# ETAPE 1 - INVENTAIRE
# ----------------------------------------------------------------------
def _norm(x):
    return re.sub(r"[^a-z0-9]", "", (x or "").lower())

def detecter_sens_chemin(rel, regles):
    """Source 1 : segments du dossier (arborescence Achats/ Ventes/ separee)."""
    segments = [x.lower() for x in re.split(r"[\\/]+", rel)[:-1]]
    for sens, cle in (("achat", "achats"), ("vente", "ventes")):
        for motif in (regles or {}).get(cle, []) or []:
            m = motif.lower()
            if any(m == seg or m in re.split(r"[\s_\-.]+", seg) for seg in segments):
                return sens
    return (regles or {}).get("defaut")

def _est_la_societe(nom, tva, conf):
    """La partie designee est-elle la societe declarante ? True / False / None."""
    tva_soc = _norm(conf.get("numero_tva"))
    # un numero de TVA renseigne (et non le gabarit "LU...") tranche seul
    if len(tva_soc) > 6 and _norm(tva):
        return _norm(tva) == tva_soc
    noms = [conf.get("denomination")] + list(conf.get("alias_societe") or [])
    cibles = [_norm(x) for x in noms if x]
    n = _norm(nom)
    if not n or not cibles:
        return None
    for c in cibles:
        if len(c) > 6 and (n.startswith(c) or c.startswith(n) or c in n or n in c):
            return True
    return False

def detecter_sens_identite(doc, conf):
    """Source 2 : qui emet, qui recoit. Fonctionne dans un dossier unique
    melangeant achats et ventes."""
    em = _est_la_societe(doc.get("emetteur"), doc.get("emetteur_tva"), conf)
    de = _est_la_societe(doc.get("destinataire"), doc.get("destinataire_tva"), conf)
    if em is True and de is not True:
        return "vente"
    if de is True and em is not True:
        return "achat"
    return None

def determiner_sens(doc, conf, regles, rel):
    """Trois sources independantes : chemin, identite des parties, sens declare
    par l'extraction. Toute divergence est bloquante ; l'absence totale aussi.
    Retourne (sens, detail_pour_exception)."""
    src = {"chemin": detecter_sens_chemin(rel, regles),
           "identite": detecter_sens_identite(doc, conf),
           "extraction": doc.get("sens")}
    retenues = {k: v for k, v in src.items() if v}
    valeurs = set(retenues.values())
    if not valeurs:
        return None, ("Sens indetermine : ni le chemin, ni l'identite des parties, "
                      "ni l'extraction ne permettent de trancher achat / vente")
    if len(valeurs) > 1:
        return None, ("Divergence de sens : "
                      + ", ".join("%s='%s'" % (k, v) for k, v in sorted(retenues.items())))
    return valeurs.pop(), None

def resoudre_source(dossier, conf, arg_source):
    """Ordre de priorite : --factures > societe.yaml:source_factures > <dossier>/factures.
    La source est typiquement un dossier client connecte via Cowork."""
    src = arg_source or (conf or {}).get("source_factures") or os.path.join(dossier, "factures")
    return os.path.abspath(os.path.expanduser(src))

def etape_inventaire(dossier, source, regles_sens=None):
    if not os.path.isdir(source):
        sys.exit("Dossier de factures introuvable : %s" % source)
    out = os.path.join(dossier, "extraction")
    txt = os.path.join(out, "textes")
    os.makedirs(txt, exist_ok=True)
    vus, inventaire, doublons, ignores = {}, [], [], 0
    for racine, dirs, fichiers in os.walk(source):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        for nom in sorted(fichiers):
            if not nom.lower().endswith(".pdf"):
                ignores += 1 if not nom.startswith(".") else 0
                continue
            chemin = os.path.join(racine, nom)
            rel = os.path.relpath(chemin, source)
            h = sha256(chemin)
            if h in vus:
                doublons.append({"fichier": rel, "doublon_de": vus[h]}); continue
            vus[h] = rel
            try:
                texte = subprocess.run(["pdftotext", "-layout", chemin, "-"],
                                       capture_output=True, timeout=30
                                       ).stdout.decode("utf-8", "replace")
            except Exception:
                texte = ""
            natif = len(texte.strip()) > 50
            if natif:
                with open(os.path.join(txt, h[:12] + ".txt"), "w", encoding="utf-8") as f:
                    f.write(texte)
            inventaire.append({"sha256": h, "fichier": rel, "chemin": chemin,
                               "sens_chemin": detecter_sens_chemin(rel, regles_sens),
                               "type": "natif" if natif else "scan",
                               "texte": (h[:12] + ".txt") if natif else None,
                               "extraction": h[:12] + ".json", "statut": "a_extraire"})
    lot = {"genere_le": dt.datetime.now().isoformat(timespec="seconds"),
           "source": source, "nb_fichiers": len(inventaire),
           "nb_doublons_ecartes": len(doublons), "nb_non_pdf_ignores": ignores,
           "doublons": doublons, "documents": inventaire}
    with open(os.path.join(out, "_inventaire.json"), "w", encoding="utf-8") as f:
        json.dump(lot, f, ensure_ascii=False, indent=2)
    n = sum(1 for d in inventaire if d["type"] == "natif")
    par_sens = defaultdict(int)
    for d in inventaire:
        par_sens[d["sens_chemin"] or "non porte par le chemin"] += 1
    print("Source : %s" % source)
    print("Inventaire : %d document(s) unique(s), %d doublon(s) ecarte(s), "
          "%d fichier(s) non-PDF ignore(s)" % (len(inventaire), len(doublons), ignores))
    print("  natifs (texte extrait) : %d" % n)
    print("  scans (lecture vision requise) : %d" % (len(inventaire) - n))
    print("  indice de sens dans le chemin : %s"
          % ", ".join("%s %d" % (k, v) for k, v in sorted(par_sens.items())))
    print("  -> %s" % os.path.join(out, "_inventaire.json"))
    return lot

# ----------------------------------------------------------------------
# ETAPE 2 - NORMALISATION
# ----------------------------------------------------------------------
def apparier_tiers(nom, referentiel):
    if not nom:
        return None
    n = re.sub(r"\s+", " ", nom).strip().lower()
    for t in referentiel.get("fournisseurs", []) + (referentiel.get("clients") or []):
        for c in [t["nom"]] + list(t.get("alias") or []):
            c = re.sub(r"\s+", " ", c).strip().lower()
            if n == c or n.startswith(c) or c.startswith(n):
                return t
    return None

def normaliser(doc, referentiel):
    t = apparier_tiers(doc.get("tiers"), referentiel)
    doc["_tiers_ref"] = t["nom"] if t else None
    for ln in doc.get("lignes", []):
        if not t:
            continue
        if t.get("libelle_standard"):
            ln["description_normalisee"] = t["libelle_standard"]; continue
        desc = (ln.get("description") or "") + " " + (doc.get("objet") or "")
        for regle in t.get("regles_libelle") or []:
            if any(m.lower() in desc.lower() for m in regle.get("si_contient", [])):
                ln["description_normalisee"] = regle["libelle"]
                if regle.get("signaler"):
                    ln.setdefault("_signalements", []).append(regle["signaler"])
                break
    return doc

# ----------------------------------------------------------------------
# ETAPE 3 - PERIMETRE (date d'emission) ET CONTROLES
# ----------------------------------------------------------------------
def decouper_perimetre(docs, debut, fin):
    """La date d'EMISSION de la facture determine l'appartenance a la periode.
    Hors periode n'est pas une erreur : le document est ecarte et trace."""
    dans, hors, sans_date = [], [], []
    for doc in docs:
        try:
            d = dt.date.fromisoformat(doc["date"])
        except Exception:
            sans_date.append(doc); continue
        doc["_date"] = d
        if debut <= d <= fin:
            dans.append(doc)
        else:
            doc["_position"] = "anterieure" if d < debut else "ulterieure"
            hors.append(doc)
    return dans, hors, sans_date

def controler(docs, conf, referentiel, debut, fin, regles_sens=None):
    exc, valides = [], []
    tol = conf.get("tolerance_arrondi", 0.02)
    admis = [round(float(x), 4) for x in conf.get("taux_admis", [])]

    def ex(doc, code, gravite, msg, ligne=None):
        exc.append({"fichier": doc.get("fichier"), "tiers": doc.get("tiers"),
                    "num_facture": doc.get("num_facture"), "code": code,
                    "gravite": gravite, "message": msg, "ligne": ligne})

    vus = {}
    for doc in docs:
        bloquant = False
        # C11 - sens : chemin, identite des parties, extraction. Divergence ou
        # absence totale = bloquant. Couvre le dossier unique achats + ventes.
        sens, detail = determiner_sens(doc, conf, regles_sens, doc.get("fichier") or "")
        if not sens:
            ex(doc, "C11", "bloquant", detail); bloquant = True
        else:
            doc["sens"] = sens
        num = (doc.get("num_facture") or "").strip()
        t = apparier_tiers(doc.get("tiers"), referentiel)
        if not num:
            ex(doc, "C5", "bloquant", "Numero de facture absent"); bloquant = True
        elif t and t.get("format_num_facture") and not re.match(t["format_num_facture"], num):
            ex(doc, "C5", "bloquant", "Numero '%s' non conforme au format attendu %s"
               % (num, t["format_num_facture"])); bloquant = True
        cle = ((doc.get("tiers") or "").lower(), num)
        if num and cle in vus:
            ex(doc, "C6", "bloquant", "Facture deja presente (%s)" % vus[cle]); bloquant = True
        elif num:
            vus[cle] = doc.get("fichier")
        dev = (doc.get("devise") or "EUR").upper()
        tc = doc.get("taux_change")
        if dev != conf.get("devise_comptable", "EUR") and not tc:
            ex(doc, "C7", "bloquant", "Devise %s sans taux de change documente" % dev)
            bloquant = True
        tc = tc or 1.0
        somme = somme_base = 0.0
        autoliq = doc.get("regime") in AUTOLIQUIDATION
        for i, ln in enumerate(doc.get("lignes", []), 1):
            taux, base, tva = ln.get("taux"), ln.get("base"), ln.get("tva")
            if taux is None or base is None:
                ex(doc, "C1", "bloquant", "Taux ou base absent", i); bloquant = True; continue
            taux, base = float(taux), float(base)
            tva = float(tva) if tva is not None else r2(base * taux)
            if round(taux, 4) not in admis:
                ex(doc, "C3", "bloquant", "Taux %g%% non admis (admis : %s)"
                   % (taux * 100, ", ".join("%g%%" % (a * 100) for a in admis)), i)
                bloquant = True
            if abs(r2(base * taux) - r2(tva)) > tol + CENT:
                ex(doc, "C1", "bloquant",
                   "Incoherence : base %.2f x %g%% = %.2f, TVA facturee %.2f"
                   % (base, taux * 100, r2(base * taux), tva), i); bloquant = True
            for s in ln.get("_signalements", []):
                ex(doc, "C9", "revue", s, i)
            somme += base + tva; somme_base += base
        ttc = doc.get("total_ttc")
        attendu = somme_base if autoliq else somme
        if ttc is not None and abs(r2(attendu) - r2(float(ttc))) > tol + CENT:
            ex(doc, "C2", "bloquant",
               "Somme des lignes %.2f != total facture %.2f%s"
               % (r2(attendu), float(ttc),
                  " (autoliquidation : TVA non facturee)" if autoliq else ""))
            bloquant = True
        if bloquant:
            continue
        for ln in doc["lignes"]:
            base = r2(float(ln["base"]) * tc); taux = float(ln["taux"])
            tva = r2(float(ln["tva"]) * tc) if ln.get("tva") is not None else r2(base * taux)
            valides.append({"date": doc["date"],
                            "tiers": doc.get("_tiers_ref") or doc.get("tiers"),
                            "num_facture": num, "pays": doc.get("pays"),
                            "description": ln.get("description_normalisee") or ln.get("description"),
                            "taux": taux, "devise": dev,
                            "montant_devise": r2(float(ln["base"])), "taux_change": tc,
                            "base": base, "tva": tva, "ttc": r2(base + tva),
                            "sens": doc.get("sens", "achat"),
                            "regime": doc.get("regime", "achat_lu"),
                            "fichier": doc.get("fichier"), "sha256": doc.get("sha256")})

    mois_periode, d = set(), debut
    while d <= fin:
        mois_periode.add((d.year, d.month))
        d = dt.date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    presents = defaultdict(set)
    for v in valides:
        dd = dt.date.fromisoformat(v["date"]); presents[v["tiers"]].add((dd.year, dd.month))
    for t in referentiel.get("fournisseurs", []):
        per = t.get("periodicite")
        if per == "mensuel":
            manq = sorted(mois_periode - presents.get(t["nom"], set()))
            if manq:
                exc.append({"fichier": None, "tiers": t["nom"], "num_facture": None,
                            "code": "C8", "gravite": "bloquant", "ligne": None,
                            "message": "Fournisseur mensuel : aucune facture pour %s"
                                       % ", ".join("%d-%02d" % m for m in manq)})
        elif per == "trimestriel" and not presents.get(t["nom"]):
            exc.append({"fichier": None, "tiers": t["nom"], "num_facture": None,
                        "code": "C8", "gravite": "revue", "ligne": None,
                        "message": "Fournisseur trimestriel : aucune facture sur la periode"})
    return valides, exc

# ----------------------------------------------------------------------
# ETAPE 4 - AGREGATION (prorata declare, non recalcule)
# ----------------------------------------------------------------------
# Regimes de chiffre d'affaires OUVRANT DROIT A DEDUCTION (numerateur du
# prorata, art. 50). Les operations exonerees sans droit a deduction
# (art. 44) en sont exclues, mais restent au denominateur.
REGIMES_CA_DROIT = {"vente_lu", "livraison_intracom", "export", "hors_ue",
                    "service_ue_rendu"}

def choisir_profil(mapping, periode):
    """Selectionne le profil de formulaire selon le type de periode."""
    suf = periode[5:]
    cle = "ANNUAL" if suf == "ANNUAL" else suf[0]
    for nom, p in mapping["formulaires"].items():
        if cle in p.get("periodes", []):
            return nom, p
    raise SystemExit("Aucun profil de formulaire pour la periode %s" % periode)

def agreger(valides, prorata_pct, origine_prorata, profil, ca_total, ca_droit, prorata_brut):
    achats = [v for v in valides if v["sens"] == "achat"]
    ventes = [v for v in valides if v["sens"] == "vente"]
    prorata = round(prorata_pct / 100.0, 4)

    tva_amont = r2(sum(v["tva"] for v in achats))
    tva_deduc = r2(tva_amont * prorata)
    tva_nd = r2(tva_amont - tva_deduc)
    # La TVA autoliquidee est DUE en aval et deductible en amont : elle figure
    # des deux cotes. Le formulaire l'confirme : total taxe due = 046 + 056 +
    # 407 + 410 + 768 + 227.
    tva_autoliq = r2(sum(v["tva"] for v in valides if v["regime"] in AUTOLIQUIDATION))
    tva_amont_achats = r2(tva_amont - tva_autoliq)
    tva_ventes = r2(sum(v["tva"] for v in ventes))
    tva_aval = r2(tva_ventes + tva_autoliq)

    cases, non_mappes = [], []
    def push(bloc, cle, base=None, taxe=None, etiquette=None):
        m = (profil.get(bloc) or {}).get(cle)
        if not m:
            non_mappes.append((etiquette or cle, base, taxe)); return
        cases.append({"bloc": bloc, "poste": cle, "libelle": m["libelle"],
                      "case_base": m.get("base"), "base": base,
                      "case_taxe": m.get("taxe"), "taxe": taxe})

    def somme(regs, taux=None):
        s = [v for v in ventes if v["regime"] in regs
             and (taux is None or abs(v["taux"] - taux) < 1e-9)]
        return (s, r2(sum(x["base"] for x in s)), r2(sum(x["tva"] for x in s)))

    # --- I. chiffre d'affaires ---------------------------------------
    ca_periode = r2(sum(v["base"] for v in ventes))
    taxable = r2(sum(v["base"] for v in ventes if v["regime"] == "vente_lu"))
    push("turnover", "ca_global", ca_periode)
    push("turnover", "total_exonerations", r2(ca_periode - taxable))
    push("turnover", "ca_taxable", taxable)
    for reg, cle in [("livraison_intracom", "livraisons_intracom"),
                     ("export", "exportations"),
                     ("exonere_art44", "exonerations_art44"),
                     ("service_ue_rendu", "services_ue_rendus"),
                     ("hors_ue", "operations_lieu_etranger")]:
        s, b, _ = somme([reg])
        if s:
            push("turnover", cle, b, None, "vente / regime '%s'" % reg)

    # --- II. taxe due -------------------------------------------------
    s, b, t = somme(["vente_lu"])
    if s:
        push("aval", "ventes_lu_total", b, t)
    for taux, cle in [(0.17, "ventes_lu_17"), (0.14, "ventes_lu_14"),
                      (0.08, "ventes_lu_08"), (0.03, "ventes_lu_03")]:
        s, b, t = somme(["vente_lu"], taux)
        if s:
            push("aval", cle, b, t)

    def sommeA(regs, taux=None):
        s = [v for v in valides if v["regime"] in regs
             and (taux is None or abs(v["taux"] - taux) < 1e-9)]
        return (s, r2(sum(x["base"] for x in s)), r2(sum(x["tva"] for x in s)))
    for regs, cle in [(("acq_intracom",), "acq_intracom_total"),
                      (("import",), "importations_total"),
                      (("service_autoliq", "service_autoliq_tiers"), "services_recus_total"),
                      (("service_autoliq",), "services_recus_ue_total"),
                      (("service_autoliq_tiers",), "services_recus_tiers_total")]:
        s, b, t = sommeA(regs)
        if s:
            push("autoliquidation", cle, b, t)
    for reg, cle in [("acq_intracom", "acq_intracom_17"), ("import", "importations_17"),
                     ("service_autoliq", "services_ue_17"),
                     ("service_autoliq_tiers", "services_tiers_17")]:
        s, b, t = sommeA((reg,), 0.17)
        if s:
            push("autoliquidation", cle, b, t)

    # --- III / IV -----------------------------------------------------
    push("amont", "tva_amont_totale", None, tva_amont)
    push("amont", "prorata_non_recuperable", None, tva_nd)
    push("amont", "tva_amont_non_deductible", None, tva_nd)
    push("amont", "tva_amont_deductible", None, tva_deduc)
    push("solde", "total_taxe_due", None, tva_aval)
    push("solde", "total_aval", None, tva_aval)
    push("solde", "total_amont_deductible", None, tva_deduc)
    push("solde", "excedent", None, r2(tva_aval - tva_deduc))

    connus = {"vente_lu", "livraison_intracom", "export", "exonere_art44",
              "service_ue_rendu", "hors_ue"}
    for reg in sorted({v["regime"] for v in ventes} - connus):
        s, b, _ = somme([reg])
        non_mappes.append(("vente / regime '%s'" % reg, b, None))

    return {"prorata_pct": prorata_pct, "prorata": prorata,
            "origine_prorata": origine_prorata, "prorata_brut": prorata_brut,
            "ca_annee_total": ca_total, "ca_annee_ouvrant_droit": ca_droit,
            "ca_total": ca_periode, "ca_ouvrant_droit":
                r2(sum(v["base"] for v in ventes if v["regime"] in REGIMES_CA_DROIT)),
            "tva_autoliquidee": tva_autoliq, "tva_amont_achats": tva_amont_achats,
            "tva_aval_ventes": tva_ventes, "tva_amont": tva_amont,
            "tva_deductible": tva_deduc, "tva_non_deductible": tva_nd,
            "tva_aval": tva_aval, "cases": cases, "non_mappes": non_mappes}

# ----------------------------------------------------------------------
# ETAPE 5 - CLASSEUR (mise en forme minimaliste, alignee sur le modele)
# ----------------------------------------------------------------------
GRAS = Font(bold=True)
CENTRE = Alignment(horizontal="center", vertical="center")
FIN = Border(bottom=Side(style="thin"))
DOUBLE = Border(bottom=Side(style="double"))
FMT_MT = "#,##0.00"; FMT_TX = "0%"; FMT_DT = "yyyy-mm-dd"
LARGEURS = {"A": 13, "B": 34, "C": 21, "D": 38, "E": 12, "F": 11,
            "G": 14, "H": 12, "I": 16, "J": 14, "K": 16, "L": 14}
COLS_ACHAT = ["Date", "Suppliers", "Invoice number", "Description", "VAT rate",
              "Currency", "Foreign amount", "Exchange rate", "Net amount",
              "VAT", "Gross amount"]
COLS_VENTE = ["Date", "Client", "Invoice number", "Country", "Description",
              "VAT rate", "Currency", "Currency amount", "Exchange rate",
              "Net amount", "VAT", "Gross amount"]

def _entete(ws, conf, titre, periode_libelle, ncols):
    ws["A1"] = conf["denomination"]; ws["A1"].font = GRAS
    ws["A2"] = "Tax number: %s" % conf.get("matricule", "")
    fin = get_column_letter(ncols)
    for r, txt in ((7, titre), (8, periode_libelle)):
        ws.merge_cells("A%d:%s%d" % (r, fin, r))
        c = ws.cell(r, 1, txt); c.font = GRAS; c.alignment = CENTRE
    for col, w in LARGEURS.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A13"

def _bloc(ws, ligne, cols, lignes, libelle_total, i_mt, section=None):
    if section:
        ws.cell(ligne, 1, section).font = GRAS; ligne += 2
    for c, v in enumerate(cols, 1):
        cell = ws.cell(ligne, c, v); cell.font = GRAS
        cell.alignment = CENTRE; cell.border = FIN
    ligne += 1
    for off, lab in enumerate(("EUR", "EUR", "EUR")):
        cell = ws.cell(ligne, i_mt + off, lab); cell.alignment = CENTRE
    ligne += 2
    tb = tt = tg = 0.0
    for v in lignes:
        base = [dt.date.fromisoformat(v["date"]), v["tiers"], v["num_facture"]]
        base += ([v["description"]] if len(cols) == 11
                 else [v["pays"], v["description"]])
        base += [v["taux"], v["devise"], v["montant_devise"], v["taux_change"],
                 v["base"], v["tva"], v["ttc"]]
        for c, val in enumerate(base, 1):
            cell = ws.cell(ligne, c, val)
            if c == 1:
                cell.number_format = FMT_DT
            elif c == i_mt - 4:                      # VAT rate -> 17%
                cell.number_format = FMT_TX; cell.alignment = CENTRE
            elif c == i_mt - 3:                      # Currency
                cell.alignment = CENTRE
            elif c in (i_mt - 2, i_mt, i_mt + 1, i_mt + 2):
                cell.number_format = FMT_MT
        tb += v["base"]; tt += v["tva"]; tg += v["ttc"]; ligne += 1
    ligne += 1
    c = ws.cell(ligne, i_mt - 1, libelle_total); c.font = GRAS
    c.alignment = Alignment(horizontal="right")
    for off, tot in enumerate((tb, tt, tg)):
        cell = ws.cell(ligne, i_mt + off, r2(tot))
        cell.font = GRAS; cell.number_format = FMT_MT; cell.border = FIN
    return ligne + 3, r2(tb), r2(tt), r2(tg)

def _grand_total(ws, ligne, i_mt, tot):
    c = ws.cell(ligne, i_mt - 1, "Grand total :"); c.font = GRAS
    c.alignment = Alignment(horizontal="right")
    for off, v in enumerate(tot):
        cell = ws.cell(ligne, i_mt + off, r2(v))
        cell.font = GRAS; cell.number_format = FMT_MT; cell.border = DOUBLE

SECTIONS_CA = [("vente_lu", "Operations subject to Luxembourg VAT"),
               ("exonere_art44", "Operations exempt under article 44"),
               ("livraison_intracom", "Intra-community supplies of goods"),
               ("service_ue_rendu", "Services to EU customers - reverse charge"),
               ("export", "Exports"),
               ("hors_ue", "Operations with place of supply outside the EU")]
LIB_TOTAL = {"vente_lu": "Total {t}:", "exonere_art44": "Total exempt art. 44:",
             "livraison_intracom": "Total EU supplies 0%:",
             "service_ue_rendu": "Total EU services 0%:",
             "export": "Total exports:", "hors_ue": "Total outside EU:"}

def generer_classeur(chemin, conf, per_lib, valides, agr):
    wb = openpyxl.Workbook()
    achats = [v for v in valides if v["sens"] == "achat"]
    ventes = [v for v in valides if v["sens"] == "vente"]

    ws = wb.active; ws.title = "Detail CA"
    _entete(ws, conf, "TURNOVER DETAIL", per_lib, 12)
    l, tot = 11, [0.0, 0.0, 0.0]
    for reg, section in SECTIONS_CA:
        s = sorted([v for v in ventes if v["regime"] == reg], key=lambda x: x["date"])
        if not s:
            continue
        for taux in sorted({v["taux"] for v in s}):
            ss = [v for v in s if v["taux"] == taux]
            lib = LIB_TOTAL[reg].format(t="%g%%" % (taux * 100))
            l, b, t, g = _bloc(ws, l, COLS_VENTE, ss, lib, 10, section)
            section = None
            tot = [tot[0] + b, tot[1] + t, tot[2] + g]
    _grand_total(ws, l, 10, tot)

    ws = wb.create_sheet("Detail TVA Lux")
    _entete(ws, conf, "DETAIL OF PURCHASE OF GOODS AND SERVICES", per_lib, 11)
    l, tot = 11, [0.0, 0.0, 0.0]
    loc = [v for v in achats if v["regime"] in ("achat_lu", "exonere_art44")]
    for taux in sorted({v["taux"] for v in loc}):
        s = sorted([v for v in loc if v["taux"] == taux], key=lambda x: x["date"])
        l, b, t, g = _bloc(ws, l, COLS_ACHAT, s, "Total %g%% :" % (taux * 100), 9)
        tot = [tot[0] + b, tot[1] + t, tot[2] + g]
    _grand_total(ws, l, 9, tot)

    ws = wb.create_sheet("Detail TVA debiteur")
    _entete(ws, conf, "DETAIL OF THE OPERATIONS TO BE SELF-ASSESSED", per_lib, 11)
    l, tot = 11, [0.0, 0.0, 0.0]
    auto = [v for v in achats if v["regime"] in AUTOLIQUIDATION]
    noms = {"acq_intracom": "Intra-community acquisitions of goods",
            "service_autoliq": "Services received from EU suppliers - reverse charge",
            "service_autoliq_tiers": "Services received from non-EU suppliers - reverse charge",
            "import": "Imports of goods"}
    for reg in sorted({v["regime"] for v in auto}):
        section = noms[reg]
        for taux in sorted({v["taux"] for v in auto if v["regime"] == reg}):
            s = [v for v in auto if v["regime"] == reg and v["taux"] == taux]
            l, b, t, g = _bloc(ws, l, COLS_ACHAT, s, "Total %g%% :" % (taux * 100), 9, section)
            section = None
            tot = [tot[0] + b, tot[1] + t, tot[2] + g]
    _grand_total(ws, l, 9, tot)

    ws = wb.create_sheet("VAT recovery right")
    _entete(ws, conf, "VAT RECOVERY RIGHT", per_lib, 11)
    lignes = [
        (11, "Turnover granting VAT recovery right", agr["ca_annee_ouvrant_droit"], FMT_MT, True),
        (12, "Total turnover of the year", agr["ca_annee_total"], FMT_MT, True),
        (14, "Recovery right percentage", agr["prorata_brut"], "0.000000", False),
        (15, "Rounded up", agr["prorata"], FMT_TX, True),
        (16, agr["origine_prorata"], None, None, False),
        (19, "VAT on purchase of goods and services", agr["tva_amont_achats"], FMT_MT, False),
        (20, "VAT on operations to be self-assessed", agr["tva_autoliquidee"], FMT_MT, False),
        (21, "Total input VAT", agr["tva_amont"], FMT_MT, True),
        (24, "Recoverable VAT", agr["tva_deductible"], FMT_MT, True),
        (25, "Non-recoverable VAT", agr["tva_non_deductible"], FMT_MT, False)]
    for r, lib, val, fmt, gras in lignes:
        c = ws.cell(r, 1, lib)
        if gras:
            c.font = GRAS
        if val is None:
            continue
        cell = ws.cell(r, 9, val); cell.number_format = fmt
        if gras:
            cell.font = GRAS
        if r == 21:
            cell.border = FIN
        if r == 25:
            cell.border = DOUBLE
    wb.save(chemin)
    return chemin

# ----------------------------------------------------------------------
# ETAPE 6 - RAPPORTS TEXTE
# ----------------------------------------------------------------------
def _bandeau(titre, conf, per_lib):
    return ["=" * 78, titre.center(78), "=" * 78, "",
            "Societe    : %s" % conf["denomination"],
            "Matricule  : %s" % conf.get("matricule", ""),
            "Periode    : %s" % per_lib,
            "Genere le  : %s" % dt.datetime.now().strftime("%d/%m/%Y %H:%M"), ""]

def rapport_exceptions(chemin, conf, per_lib, exc, hors, sans_date):
    L = _bandeau("EXCEPTIONS", conf, per_lib)
    bloq = [e for e in exc if e["gravite"] == "bloquant"]
    revue = [e for e in exc if e["gravite"] == "revue"]
    L += ["Bloquantes : %d   |   A revoir : %d   |   Hors periode : %d"
          % (len(bloq), len(revue), len(hors)), "",
          "Aucune ligne bloquante n'est integree aux annexes.",
          "",
          "Ce livrable - annexes, correspondance eCDF et present rapport - est soumis",
          "au client pour accord avant depot. L'accord du client sur le lot de factures",
          "retenu vaut confirmation de completude.",
          "-" * 78, ""]
    for lib, lot in (("BLOQUANTES", bloq), ("A REVOIR", revue)):
        L += ["%s (%d)" % (lib, len(lot)), "-" * 78]
        if not lot:
            L += ["  neant", ""]
        for e in sorted(lot, key=lambda x: x["code"]):
            ref = " / ".join(x for x in [e.get("tiers"), e.get("num_facture")] if x)
            L.append("  [%s] %s" % (e["code"], ref or "-"))
            L.append("        %s" % e["message"])
            if e.get("fichier"):
                L.append("        fichier : %s%s"
                         % (e["fichier"], "  (ligne %s)" % e["ligne"] if e.get("ligne") else ""))
            L.append("")
    L += ["HORS PERIODE - REGISTRE DE REPRISE (%d)" % len(hors), "-" * 78]
    if not hors:
        L += ["  neant", ""]
    for d in sorted(hors, key=lambda x: x["date"]):
        L.append("  %s  %-28s %-20s  periode %s"
                 % (d["date"], (d.get("tiers") or "")[:28],
                    (d.get("num_facture") or "")[:20], d["_position"]))
        L.append("        %s" % (d.get("fichier") or ""))
    if hors:
        L += ["", "  Verifier pour chacune si elle a deja ete declaree sur sa periode",
              "  d'emission. Si non : rectificative ou regularisation.", ""]
    if sans_date:
        L += ["", "DATE ILLISIBLE OU ABSENTE (%d)" % len(sans_date), "-" * 78]
        for d in sans_date:
            L.append("  %s" % (d.get("fichier") or d.get("num_facture")))
    L += ["", "=" * 78, "Fin du rapport."]
    open(chemin, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return chemin

def rapport_ecdf(chemin, conf, per_lib, agr, nom_profil, profil):
    L = _bandeau("CORRESPONDANCE VERS LA DECLARATION eCDF", conf, per_lib)
    L += ["Formulaire      : %s (%s)" % (profil.get("libelle"), nom_profil),
          "Version source  : %s" % profil.get("version_source"),
          "STATUT MAPPING  : %s" % profil.get("statut"), ""]
    if profil.get("source"):
        L += ["  " + l.strip() for l in profil["source"].strip().splitlines()] + [""]
    if profil.get("statut") != "VALIDE":
        L += ["*" * 78,
              "  ATTENTION : les numeros de cases n'ont pas ete valides contre le",
              "  formulaire en vigueur. Aucun depot ne doit etre effectue sur cette",
              "  base avant verification.",
              "*" * 78, ""]
    L += ["-" * 78,
          "%-6s %-42s %14s" % ("CASE", "LIBELLE", "MONTANT EUR"), "-" * 78]
    bloc_courant = None
    for k in agr["cases"]:
        if k["bloc"] != bloc_courant:
            bloc_courant = k["bloc"]
            L += ["", bloc_courant.upper()]
        if k.get("case_base") and k.get("base") is not None:
            L.append("%-6s %-42s %14s"
                     % (k["case_base"], k["libelle"][:42] + " (base)", "%.2f" % k["base"]))
        if k.get("case_taxe") and k.get("taxe") is not None:
            L.append("%-6s %-42s %14s"
                     % (k["case_taxe"], k["libelle"][:42], "%.2f" % k["taxe"]))
    L += ["", "-" * 78, "COMPOSITION DE LA TAXE", "-" * 78,
          "  (la TVA autoliquidee est due en aval ET deductible en amont ;",
          "   le formulaire le confirme : 076 = 046 + 056 + 407 + 410 + 768 + 227)",
          "",
          "  TVA collectee sur ventes    : %10.2f" % agr["tva_aval_ventes"],
          "  TVA autoliquidee (due)      : %10.2f" % agr["tva_autoliquidee"],
          "  Total TVA en aval           : %10.2f" % agr["tva_aval"],
          "",
          "  TVA sur achats              : %10.2f" % agr["tva_amont_achats"],
          "  TVA autoliquidee (deductible): %9.2f" % agr["tva_autoliquidee"],
          "  Total TVA en amont          : %10.2f" % agr["tva_amont"],
          "", "-" * 78, "DROIT A DEDUCTION (prorata general, art. 50)", "-" * 78,
          "  CA de l'annee - total        : %10.2f" % (agr["ca_annee_total"] or 0),
          "  CA ouvrant droit a deduction : %10.2f" % (agr["ca_annee_ouvrant_droit"] or 0),
          "  Prorata brut                 : %s"
          % ("%.6f" % agr["prorata_brut"] if agr["prorata_brut"] is not None else "-"),
          "  Prorata applique             : %g %%   (%s)"
          % (agr["prorata_pct"], agr["origine_prorata"]),
          "",
          "  TVA en amont totale     : %.2f" % agr["tva_amont"],
          "  TVA deductible          : %.2f" % agr["tva_deductible"],
          "  TVA non deductible      : %.2f" % agr["tva_non_deductible"]]
    if agr["non_mappes"]:
        L += ["", "*" * 78, "MONTANTS SANS CASE MAPPEE - A AFFECTER MANUELLEMENT", "*" * 78]
        for poste, base, taxe in agr["non_mappes"]:
            L.append("  %-46s base %10s   taxe %10s"
                     % (poste, "%.2f" % base if base is not None else "-",
                        "%.2f" % taxe if taxe is not None else "-"))
    L += ["", "=" * 78, "Fin du rapport."]
    open(chemin, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return chemin

# ----------------------------------------------------------------------
# PILOTAGE
# ----------------------------------------------------------------------
def etape_annexes(dossier, racine, periode=None, prorata=None, exercice=None,
                  sortie=None):
    conf = charger_yaml(os.path.join(dossier, "societe.yaml"))
    referentiel = charger_yaml(os.path.join(dossier, "referentiel.yaml"))
    mapping = charger_yaml(os.path.join(racine, "config", "mapping_ecdf.yaml"))
    print("\n%s\n%s\n%s" % ("=" * 60, conf["denomination"].center(60), "=" * 60))
    periode = periode or demander_periode(conf.get("periodicite"))
    debut, fin, per_lib = bornes_periode(periode)
    nom_profil, profil = choisir_profil(mapping, periode)
    print("  -> %s : %s au %s" % (per_lib, debut.strftime("%d/%m/%Y"), fin.strftime("%d/%m/%Y")))
    print("  -> formulaire %s (%s)" % (profil["libelle"], profil["statut"]))

    ext = os.path.join(dossier, "extraction")
    inv = json.load(open(os.path.join(ext, "_inventaire.json"), encoding="utf-8"))
    docs, absents = [], []
    for d in inv["documents"]:
        p = os.path.join(ext, d["extraction"])
        if not os.path.exists(p):
            absents.append(d["fichier"]); continue
        doc = json.load(open(p, encoding="utf-8"))
        doc.setdefault("fichier", d["fichier"]); doc.setdefault("sha256", d["sha256"])
        docs.append(normaliser(doc, referentiel))

    dans, hors, sans_date = decouper_perimetre(docs, debut, fin)
    valides, exc = controler(dans, conf, referentiel, debut, fin,
                             conf.get("detection_sens"))
    for f in absents:
        exc.append({"fichier": f, "tiers": None, "num_facture": None, "code": "C0",
                    "gravite": "bloquant", "message": "Extraction absente", "ligne": None})

    out_prov = os.path.abspath(os.path.expanduser(
        sortie or conf.get("dossier_sortie") or os.path.join(dossier, "annexes")))
    ca_total, ca_droit, autres = ca_de_l_annee(out_prov, periode[:4], valides, periode)
    if prorata is None:
        prorata, origine, brut = demander_prorata(conf, ca_total, ca_droit,
                                                  periode[:4], autres)
    else:
        brut = prorata_art50(ca_droit, ca_total)[0]
        origine = "impose en ligne de commande%s" % (" (%s)" % exercice if exercice else "")
    if not periode.endswith("ANNUAL"):
        origine += " - PROVISOIRE : CA partiel de l'exercice, a regulariser sur l'annuelle"
    print("  -> prorata retenu : %g %%  (%s)\n" % (prorata, origine))
    agr = agreger(valides, prorata, origine, profil, ca_total, ca_droit, brut)
    base_nom = conf["sortie"]["nom_fichier"].format(
        code=conf["code"], annee=periode[:4], periode=periode[5:],
        date_prod=dt.date.today().strftime("%Y%m%d")).replace(".xlsx", "")
    out = out_prov
    os.makedirs(out, exist_ok=True)
    f_xlsx = generer_classeur(os.path.join(out, base_nom + ".xlsx"), conf, per_lib, valides, agr)
    f_exc = rapport_exceptions(os.path.join(out, base_nom + "-EXCEPTIONS.txt"),
                               conf, per_lib, exc, hors, sans_date)
    f_ecdf = rapport_ecdf(os.path.join(out, base_nom + "-CASES-ECDF.txt"),
                          conf, per_lib, agr, nom_profil, profil)
    # instantane machine du declare : socle de la reconciliation annuelle
    nb_bloq = sum(1 for e in exc if e["gravite"] == "bloquant")
    f_dec = os.path.join(out, base_nom + "-DECLARE.json")
    json.dump({"code": conf["code"], "denomination": conf["denomination"],
               "periode": periode, "periode_libelle": per_lib,
               "debut": debut.isoformat(), "fin": fin.isoformat(),
               "genere_le": dt.datetime.now().isoformat(timespec="seconds"),
               "prorata_pct": agr["prorata_pct"], "origine_prorata": agr["origine_prorata"],
               "formulaire": nom_profil,
               "exceptions_bloquantes": nb_bloq,
               "hors_periode": [{"date": d["date"], "tiers": d.get("tiers"),
                                 "num_facture": d.get("num_facture"),
                                 "position": d["_position"], "fichier": d.get("fichier")}
                                for d in hors],
               "totaux": {"tva_amont": agr["tva_amont"], "tva_aval": agr["tva_aval"],
                          "tva_deductible": agr["tva_deductible"]},
               "lignes": valides},
              open(f_dec, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    nb_b = sum(1 for e in exc if e["gravite"] == "bloquant")
    print("Documents dans le perimetre : %d   hors periode : %d" % (len(dans), len(hors)))
    print("  lignes retenues       : %d" % len(valides))
    print("  exceptions bloquantes : %d" % nb_b)
    print("  exceptions a revoir   : %d" % (len(exc) - nb_b))
    print("  TVA amont %.2f | deductible %.2f | non deductible %.2f"
          % (agr["tva_amont"], agr["tva_deductible"], agr["tva_non_deductible"]))
    if agr["non_mappes"]:
        print("  ATTENTION : %d poste(s) sans case eCDF mappee" % len(agr["non_mappes"]))
    for f in (f_xlsx, f_exc, f_ecdf, f_dec):
        print("  -> %s" % os.path.basename(f))
    return f_xlsx

def main():
    ap = argparse.ArgumentParser(description="Annexes de declaration TVA (Luxembourg)")
    ap.add_argument("etape", choices=["inventaire", "annexes"])
    ap.add_argument("--dossier", required=True)
    ap.add_argument("--periode", help="non interactif : 2025-Q3 | 2025-M08 | 2025-ANNUAL")
    ap.add_argument("--prorata", type=float, help="non interactif : prorata en %% (ex. 92)")
    ap.add_argument("--exercice", help="non interactif : exercice de reference du prorata")
    ap.add_argument("--factures", help="dossier source des PDF (dossier client Cowork). "
                                       "Defaut : societe.yaml:source_factures, sinon <dossier>/factures")
    ap.add_argument("--sortie", help="dossier de depot des livrables. "
                                     "Defaut : societe.yaml:dossier_sortie, sinon <dossier>/annexes")
    a = ap.parse_args()
    racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    conf_p = os.path.join(a.dossier, "societe.yaml")
    conf = charger_yaml(conf_p) if os.path.exists(conf_p) else {}
    if a.etape == "inventaire":
        etape_inventaire(a.dossier, resoudre_source(a.dossier, conf, a.factures),
                         conf.get("detection_sens"))
    else:
        etape_annexes(a.dossier, racine, a.periode, a.prorata, a.exercice, a.sortie)

if __name__ == "__main__":
    main()
