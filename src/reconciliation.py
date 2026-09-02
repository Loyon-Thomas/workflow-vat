#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reconciliation post-comptabilisation - declaration TVA annuelle recapitulative.

L'annuelle est theoriquement le cumul des declarations periodiques de l'exercice.
Ce module verifie que c'est vrai, puis confronte l'ensemble declare a la
comptabilite une fois celle-ci rattrapee.

DEUX AXES
  A. Coherence interne : les periodiques cumulees font-elles l'annuelle ?
     -> periodes manquantes, doublons inter-periodes, exceptions non soldees,
        factures inscrites au registre de reprise et jamais reprises.
  B. Declare vs comptabilite : quatre categories exhaustives.
     -> concordant / ecart de montant / declare non comptabilise /
        COMPTABILISE NON DECLARE (le plus grave : TVA jamais declaree).

USAGE
  python3 reconciliation.py --dossier <chemin> --annee 2025 \\
                           [--gl <export.xlsx|csv> [--feuille GL]]
"""
import argparse, csv, datetime as dt, glob, json, os, re, sys
from collections import defaultdict

try:
    import yaml
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side
except ImportError:
    sys.exit("PyYAML et openpyxl requis")

GRAS = Font(bold=True); TITRE = Font(bold=True, size=12)
CENTRE = Alignment(horizontal="center"); FIN = Border(bottom=Side(style="thin"))
FMT = "#,##0.00"

def r2(x):
    return None if x is None else round(float(x) + 1e-9, 2)

def norm(x):
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())

def charger_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)

# ----------------------------------------------------------------------
# AXE A - cumul des periodiques
# ----------------------------------------------------------------------
def periodes_attendues(periodicite, annee):
    if periodicite == "mensuelle":
        return ["%d-M%02d" % (annee, m) for m in range(1, 13)]
    if periodicite == "trimestrielle":
        return ["%d-Q%d" % (annee, q) for q in range(1, 5)]
    return ["%d-ANNUAL" % annee]

def charger_declares(dossier_annexes, annee):
    lots = []
    for f in sorted(glob.glob(os.path.join(dossier_annexes, "**", "*-DECLARE.json"),
                              recursive=True)):
        d = json.load(open(f, encoding="utf-8"))
        if d.get("periode", "").startswith(str(annee)) and not d["periode"].endswith("ANNUAL"):
            d["_fichier"] = os.path.basename(f)
            lots.append(d)
    # si une periode a ete rejouee, seule la generation la plus recente compte
    par_periode = {}
    for d in sorted(lots, key=lambda x: x["genere_le"]):
        par_periode[d["periode"]] = d
    return [par_periode[k] for k in sorted(par_periode)]

def controler_cumul(lots, periodicite, annee):
    anomalies = []
    presentes = {d["periode"] for d in lots}
    for p in periodes_attendues(periodicite, annee):
        if p not in presentes:
            anomalies.append({"code": "R1", "gravite": "bloquant", "periode": p,
                              "message": "Aucune declaration periodique produite pour "
                                         "cette periode"})
    for d in lots:
        if d.get("exceptions_bloquantes"):
            anomalies.append({"code": "R2", "gravite": "bloquant", "periode": d["periode"],
                              "message": "%d exception(s) bloquante(s) non soldee(s) a la "
                                         "generation du %s"
                                         % (d["exceptions_bloquantes"], d["genere_le"][:10])})
    # doublons inter-periodes
    vu = defaultdict(list)
    for d in lots:
        for l in d["lignes"]:
            vu[(norm(l["tiers"]), norm(l["num_facture"]), l["taux"])].append(d["periode"])
    for (t, n, tx), pers in vu.items():
        if len(set(pers)) > 1:
            anomalies.append({"code": "R3", "gravite": "bloquant", "periode": ", ".join(sorted(set(pers))),
                              "message": "Facture %s declaree dans plusieurs periodes "
                                         "au taux %g%%" % (n, tx * 100)})
    # registre de reprise jamais repris
    declarees = {(norm(l["tiers"]), norm(l["num_facture"]))
                 for d in lots for l in d["lignes"]}
    signale = set()
    for d in lots:
        for h in d.get("hors_periode", []):
            cle = (norm(h.get("tiers")), norm(h.get("num_facture")))
            if cle not in declarees and cle not in signale:
                signale.add(cle)
                anomalies.append({"code": "R4", "gravite": "bloquant",
                                  "periode": d["periode"],
                                  "message": "Facture %s (%s) ecartee en %s et reprise dans "
                                             "aucune periode de l'exercice"
                                             % (h.get("num_facture") or "sans numero",
                                                h.get("tiers"), d["periode"])})
    return anomalies

def controler_collisions(factures):
    """R5 - un meme numero de facture apparait a plusieurs dates chez un meme
    tiers. Le cumul les traite comme des factures distinctes (la date entre dans
    la cle), mais l'ambiguite doit etre tranchee : numerotation fournisseur
    defaillante, ou erreur de saisie."""
    par_num = defaultdict(list)
    for f in factures.values():
        par_num[(norm(f["tiers"]), norm(f["num_facture"]))].append(f)
    return [{"code": "R5", "gravite": "revue",
             "periode": ", ".join(sorted({p for f in lot for p in f["periodes"]})),
             "message": "Numero %s (%s) apparait a %d dates differentes : %s"
                        % (lot[0]["num_facture"], lot[0]["tiers"], len(lot),
                           ", ".join(sorted(f["date"] for f in lot)))}
            for lot in par_num.values() if len(lot) > 1]

def cumuler(lots):
    """Cumul au niveau facture : une entree par (tiers, n de facture)."""
    factures = {}
    for d in lots:
        for l in d["lignes"]:
            cle = (norm(l["tiers"]), norm(l["num_facture"]), l["date"])
            f = factures.setdefault(cle, {
                "tiers": l["tiers"], "num_facture": l["num_facture"], "date": l["date"],
                "sens": l["sens"], "regime": l["regime"], "base": 0.0, "tva": 0.0,
                "periodes": set(), "taux": set(), "dates": set(),
                "fichier": l.get("fichier")})
            f["base"] += l["base"]; f["tva"] += l["tva"]
            f["periodes"].add(d["periode"]); f["taux"].add(l["taux"])
            f["dates"].add(l["date"]); f["date"] = min(f["date"], l["date"])
    for f in factures.values():
        f["base"] = r2(f["base"]); f["tva"] = r2(f["tva"])
        f["periodes"] = sorted(f["periodes"]); f["taux"] = sorted(f["taux"])
        f["dates"] = sorted(f["dates"])
    return factures

# ----------------------------------------------------------------------
# AXE B - comptabilite
# ----------------------------------------------------------------------
def _colonne(entetes, profil, cle):
    cands = [profil.get(cle)] + list(profil.get(cle + "_alias") or [])
    for c in cands:
        if c and c in entetes:
            return entetes.index(c)
    n = {norm(e): i for i, e in enumerate(entetes)}
    for c in cands:
        if c and norm(c) in n:
            return n[norm(c)]
    return None

def charger_gl(chemin, feuille, profil, selection, annee):
    if chemin.lower().endswith((".csv", ".txt")):
        with open(chemin, encoding="utf-8-sig") as f:
            premiere = f.readline()
            f.seek(0)
            sep = ";" if premiere.count(";") > premiere.count(",") else ","
            lignes = list(csv.reader(f, delimiter=sep))
        entetes = [c.strip() for c in lignes[0]]
        corps = lignes[1:]
    else:
        wb = openpyxl.load_workbook(chemin, data_only=True, read_only=True)
        ws = wb[feuille] if feuille and feuille in wb.sheetnames else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        entetes = ["" if x is None else str(x).strip() for x in next(it)]
        corps = list(it)
    idx = {k: _colonne(entetes, profil, k) for k in
           ("compte", "journal", "piece", "date", "libelle", "base", "tva")}
    manquants = [k for k, v in idx.items() if v is None]
    if manquants:
        sys.exit("Colonnes introuvables dans l'export GL : %s\nEn-tetes lus : %s"
                 % (", ".join(manquants), entetes))
    jx = set(selection["journaux_achats"]) | set(selection["journaux_ventes"])
    pref = tuple(selection["prefixes_comptes"])
    pieces = {}
    for r in corps:
        def val(k):
            i = idx[k]
            return r[i] if i is not None and i < len(r) else None
        jr = str(val("journal") or "").strip().upper()
        cp = str(val("compte") or "").strip()
        if jr not in jx or not cp.startswith(pref):
            continue
        base = float(val("base") or 0); tva = float(val("tva") or 0)
        if abs(base) < 0.005 and abs(tva) < 0.005:
            continue
        d = val("date")
        if isinstance(d, dt.datetime):
            d = d.date()
        elif isinstance(d, str):
            try:
                d = dt.date.fromisoformat(d[:10])
            except Exception:
                d = None
        if d and d.year != annee:
            continue
        pc = str(val("piece") or "").strip()
        p = pieces.setdefault(pc, {"piece": pc, "journal": jr, "date": d, "base": 0.0,
                                   "tva": 0.0, "libelles": [], "comptes": set(),
                                   "sens": "achat" if jr in selection["journaux_achats"] else "vente"})
        p["base"] += base; p["tva"] += tva
        p["comptes"].add(cp)
        lb = str(val("libelle") or "").strip()
        if lb and lb not in p["libelles"]:
            p["libelles"].append(lb)
        if d and (p["date"] is None or d < p["date"]):
            p["date"] = d
    for p in pieces.values():
        p["base"] = r2(p["base"]); p["tva"] = r2(p["tva"])
        p["libelle"] = " | ".join(p["libelles"])
        p["comptes"] = ", ".join(sorted(p["comptes"]))
    return pieces

def rapprocher(factures, pieces, tol, tol_jours):
    """Cascade a trois niveaux, du plus sur au plus faible. Chaque piece et
    chaque facture n'est consommee qu'une fois."""
    res = {"concordant": [], "ecart": [], "declare_non_compta": [], "compta_non_declare": []}
    libres_f = dict(factures); libres_p = dict(pieces)

    def apparier(f_cle, p_cle, confiance):
        f = libres_f.pop(f_cle); p = libres_p.pop(p_cle)
        db, dt_ = r2(f["base"] - p["base"]), r2(f["tva"] - p["tva"])
        ligne = {"tiers": f["tiers"], "num_facture": f["num_facture"], "date": f["date"],
                 "periodes": ", ".join(f["periodes"]), "piece": p["piece"],
                 "compte": p["comptes"], "libelle_gl": p["libelle"][:70],
                 "base_declaree": f["base"], "base_compta": p["base"], "ecart_base": db,
                 "tva_declaree": f["tva"], "tva_compta": p["tva"], "ecart_tva": dt_,
                 "confiance": confiance}
        res["concordant" if abs(db) <= tol and abs(dt_) <= tol else "ecart"].append(ligne)

    # niveau 1 : le numero de facture apparait dans le libelle de la piece
    for f_cle in list(libres_f):
        n = norm(libres_f[f_cle]["num_facture"])
        if len(n) < 4:
            continue
        for p_cle in list(libres_p):
            p = libres_p[p_cle]
            if n in norm(p["libelle"]) or n == norm(p["piece"]):
                apparier(f_cle, p_cle, "certain (n de facture)"); break
    # niveau 2 : montants concordants et dates proches
    for f_cle in list(libres_f):
        f = libres_f[f_cle]
        for p_cle in list(libres_p):
            p = libres_p[p_cle]
            if abs(f["base"] - p["base"]) <= tol and abs(f["tva"] - p["tva"]) <= tol:
                try:
                    ecart_j = abs((dt.date.fromisoformat(f["date"]) - p["date"]).days)
                except Exception:
                    continue
                if ecart_j <= tol_jours:
                    apparier(f_cle, p_cle, "probable (montants + date)"); break
    # niveau 3 : montants concordants seuls
    for f_cle in list(libres_f):
        f = libres_f[f_cle]
        for p_cle in list(libres_p):
            p = libres_p[p_cle]
            if abs(f["base"] - p["base"]) <= tol and abs(f["tva"] - p["tva"]) <= tol:
                apparier(f_cle, p_cle, "faible (montants seuls)"); break

    for f in libres_f.values():
        res["declare_non_compta"].append(f)
    for p in libres_p.values():
        res["compta_non_declare"].append(p)
    return res

# ----------------------------------------------------------------------
# SORTIES
# ----------------------------------------------------------------------
def _feuille(wb, titre, colonnes, lignes, largeurs=None):
    ws = wb.create_sheet(titre[:31])
    ws["A1"] = titre; ws["A1"].font = TITRE
    for c, h in enumerate(colonnes, 1):
        cell = ws.cell(3, c, h); cell.font = GRAS; cell.alignment = CENTRE; cell.border = FIN
    for i, ligne in enumerate(lignes):
        for c, v in enumerate(ligne, 1):
            cell = ws.cell(4 + i, c, v)
            if isinstance(v, float):
                cell.number_format = FMT
    for c, w in enumerate(largeurs or [], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.freeze_panes = "A4"
    return ws

def generer_xlsx(chemin, conf, annee, lots, anomalies, factures, res, gl_present):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Synthese"
    ws["A1"] = "RECONCILIATION POST-COMPTABILISATION"; ws["A1"].font = TITRE
    lignes = [("Societe", conf["denomination"]), ("Matricule", conf.get("matricule")),
              ("Exercice", annee),
              ("Genere le", dt.datetime.now().strftime("%d/%m/%Y %H:%M")), ("", ""),
              ("AXE A - CUMUL DES PERIODIQUES", ""),
              ("Periodes declarees", len(lots)),
              ("Periodes attendues", len(periodes_attendues(conf.get("periodicite"), annee))),
              ("Anomalies de cumul", len(anomalies)),
              ("Factures distinctes cumulees", len(factures)),
              ("Base cumulee achats", r2(sum(f["base"] for f in factures.values()
                                             if f["sens"] == "achat"))),
              ("TVA cumulee achats", r2(sum(f["tva"] for f in factures.values()
                                            if f["sens"] == "achat"))),
              ("Base cumulee ventes", r2(sum(f["base"] for f in factures.values()
                                             if f["sens"] == "vente"))),
              ("TVA cumulee ventes", r2(sum(f["tva"] for f in factures.values()
                                            if f["sens"] == "vente"))), ("", "")]
    if gl_present:
        e_tva = r2(sum(l["ecart_tva"] for l in res["ecart"]))
        nd_tva = r2(sum(p["tva"] for p in res["compta_non_declare"]))
        dn_tva = r2(sum(f["tva"] for f in res["declare_non_compta"]))
        lignes += [("AXE B - DECLARE vs COMPTABILITE", ""),
                   ("Concordants", len(res["concordant"])),
                   ("Ecarts de montant", len(res["ecart"])),
                   ("Ecart TVA cumule sur ces lignes", e_tva),
                   ("Declares non comptabilises", len(res["declare_non_compta"])),
                   ("TVA concernee", dn_tva),
                   ("COMPTABILISES NON DECLARES", len(res["compta_non_declare"])),
                   ("TVA JAMAIS DECLAREE", nd_tva)]
    else:
        lignes += [("AXE B", "non execute - aucun export comptable fourni")]
    for i, (a, b) in enumerate(lignes):
        ws.cell(3 + i, 1, a).font = GRAS if a.isupper() and a else Font()
        cell = ws.cell(3 + i, 2, b)
        if isinstance(b, float):
            cell.number_format = FMT
    ws.column_dimensions["A"].width = 38; ws.column_dimensions["B"].width = 34

    _feuille(wb, "Anomalies de cumul", ["Code", "Gravite", "Periode", "Message"],
             [(a["code"], a["gravite"], a["periode"], a["message"]) for a in anomalies],
             [8, 12, 22, 100])
    if gl_present:
        cols = ["Tiers", "N facture", "Date", "Periodes", "Piece GL", "Comptes",
                "Base declaree", "Base compta", "Ecart base", "TVA declaree",
                "TVA compta", "Ecart TVA", "Confiance", "Libelle GL"]
        lg = [30, 20, 12, 14, 14, 20, 14, 14, 12, 14, 14, 12, 24, 60]
        for titre, cle in (("Ecarts de montant", "ecart"), ("Concordants", "concordant")):
            _feuille(wb, titre, cols,
                     [(l["tiers"], l["num_facture"], l["date"], l["periodes"], l["piece"],
                       l["compte"], l["base_declaree"], l["base_compta"], l["ecart_base"],
                       l["tva_declaree"], l["tva_compta"], l["ecart_tva"], l["confiance"],
                       l["libelle_gl"]) for l in res[cle]], lg)
        _feuille(wb, "Declare non comptabilise",
                 ["Tiers", "N facture", "Date", "Periodes", "Sens", "Base", "TVA", "Fichier"],
                 [(f["tiers"], f["num_facture"], f["date"], ", ".join(f["periodes"]),
                   f["sens"], f["base"], f["tva"], f.get("fichier"))
                  for f in sorted(res["declare_non_compta"], key=lambda x: -abs(x["tva"]))],
                 [30, 20, 12, 14, 10, 14, 14, 40])
        _feuille(wb, "Compta non declare",
                 ["Piece GL", "Journal", "Date", "Sens", "Comptes", "Base", "TVA", "Libelle GL"],
                 [(p["piece"], p["journal"], p["date"], p["sens"], p["comptes"],
                   p["base"], p["tva"], p["libelle"][:90])
                  for p in sorted(res["compta_non_declare"], key=lambda x: -abs(x["tva"]))],
                 [16, 10, 12, 10, 22, 14, 14, 90])
    _feuille(wb, "Cumul par facture",
             ["Tiers", "N facture", "Date", "Sens", "Regime", "Taux", "Periodes", "Base", "TVA"],
             [(f["tiers"], f["num_facture"], f["date"], f["sens"], f["regime"],
               ", ".join("%g%%" % (t * 100) for t in f["taux"]), ", ".join(f["periodes"]),
               f["base"], f["tva"]) for f in sorted(factures.values(), key=lambda x: x["date"])],
             [30, 20, 12, 10, 18, 14, 16, 14, 14])
    wb.save(chemin)
    return chemin

def generer_txt(chemin, conf, annee, lots, anomalies, factures, res, gl_present):
    L = ["=" * 78, "RECONCILIATION POST-COMPTABILISATION".center(78),
         ("Exercice %s" % annee).center(78), "=" * 78, "",
         "Societe   : %s" % conf["denomination"],
         "Matricule : %s" % conf.get("matricule", ""),
         "Genere le : %s" % dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "",
         "-" * 78, "AXE A - LES PERIODIQUES CUMULEES FONT-ELLES L'ANNUELLE ?", "-" * 78,
         "  Periodes declarees : %d / %d attendues"
         % (len(lots), len(periodes_attendues(conf.get("periodicite"), annee))),
         "  Factures distinctes cumulees : %d" % len(factures),
         "  Anomalies de cumul : %d" % len(anomalies), ""]
    if anomalies:
        for a in anomalies:
            L.append("  [%s] %s" % (a["code"], a["periode"]))
            L.append("        %s" % a["message"])
        L.append("")
    else:
        L += ["  Aucune anomalie : le cumul des periodiques est exploitable en l'etat.", ""]
    L += ["  Cumul achats : base %12.2f   TVA %10.2f"
          % (sum(f["base"] for f in factures.values() if f["sens"] == "achat"),
             sum(f["tva"] for f in factures.values() if f["sens"] == "achat")),
          "  Cumul ventes : base %12.2f   TVA %10.2f"
          % (sum(f["base"] for f in factures.values() if f["sens"] == "vente"),
             sum(f["tva"] for f in factures.values() if f["sens"] == "vente")), ""]
    L += ["-" * 78, "AXE B - DECLARE vs COMPTABILITE", "-" * 78]
    if not gl_present:
        L += ["  Non execute : aucun export comptable fourni (--gl).", ""]
    else:
        tot = sum(len(v) for v in res.values())
        L += ["  %-34s %5d  soit %5.1f %%" % ("Concordants", len(res["concordant"]),
                                              100.0 * len(res["concordant"]) / max(tot, 1)),
              "  %-34s %5d" % ("Ecarts de montant", len(res["ecart"])),
              "  %-34s %5d" % ("Declares non comptabilises", len(res["declare_non_compta"])),
              "  %-34s %5d" % ("COMPTABILISES NON DECLARES", len(res["compta_non_declare"])), ""]
        if res["ecart"]:
            L += ["", "ECARTS DE MONTANT (%d)" % len(res["ecart"]), "-" * 78,
                  "  Meme facture des deux cotes, montants divergents.", ""]
            for l in sorted(res["ecart"], key=lambda x: -abs(x["ecart_tva"])):
                L.append("  %-26s %-18s piece %-10s [%s]"
                         % (l["tiers"][:26], l["num_facture"][:18], l["piece"], l["confiance"]))
                L.append("        base  declaree %10.2f   compta %10.2f   ecart %+9.2f"
                         % (l["base_declaree"], l["base_compta"], l["ecart_base"]))
                L.append("        TVA   declaree %10.2f   compta %10.2f   ecart %+9.2f"
                         % (l["tva_declaree"], l["tva_compta"], l["ecart_tva"]))
            L += ["", "  Ecart TVA cumule : %+.2f"
                  % sum(l["ecart_tva"] for l in res["ecart"]), ""]
        if res["declare_non_compta"]:
            L += ["", "DECLARES NON COMPTABILISES (%d)" % len(res["declare_non_compta"]),
                  "-" * 78,
                  "  Declares a l'administration, absents de la comptabilite.",
                  "  Soit la piece n'a pas ete comptabilisee, soit elle l'a ete",
                  "  sous une reference que le rapprochement n'atteint pas.", ""]
            for f in sorted(res["declare_non_compta"], key=lambda x: -abs(x["tva"])):
                L.append("  %s  %-26s %-18s base %10.2f  TVA %9.2f  [%s]"
                         % (f["date"], f["tiers"][:26], (f["num_facture"] or "")[:18],
                            f["base"], f["tva"], ", ".join(f["periodes"])))
            L += ["", "  TVA concernee : %.2f"
                  % sum(f["tva"] for f in res["declare_non_compta"]), ""]
        if res["compta_non_declare"]:
            L += ["", "*" * 78,
                  "COMPTABILISES NON DECLARES (%d)" % len(res["compta_non_declare"]),
                  "*" * 78,
                  "  Presents en comptabilite, absents de toute declaration periodique.",
                  "  C'est le manquement declaratif : la TVA correspondante n'a jamais",
                  "  ete portee a l'administration. A traiter en rectificative ou en",
                  "  regularisation sur la periode courante.", ""]
            for p in sorted(res["compta_non_declare"], key=lambda x: -abs(x["tva"])):
                L.append("  %s  piece %-10s %-8s base %10.2f  TVA %9.2f"
                         % (p["date"], p["piece"], p["journal"], p["base"], p["tva"]))
                L.append("        %s" % p["libelle"][:70])
            L += ["", "  TVA JAMAIS DECLAREE : %.2f"
                  % sum(p["tva"] for p in res["compta_non_declare"]), ""]
    L += ["", "-" * 78, "LIMITES", "-" * 78,
          "  Le rapprochement s'appuie sur le numero de facture lu dans le libelle",
          "  de la piece comptable, puis a defaut sur les montants et la date. Un",
          "  rapprochement de confiance 'faible' (montants seuls) peut associer deux",
          "  operations distinctes de meme montant : verifier ces lignes.",
          "  Le module ne requalifie aucune operation et ne juge pas si un ecart",
          "  provient de la declaration ou de la comptabilite.", "",
          "=" * 78, "Fin du rapport."]
    open(chemin, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return chemin

# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Reconciliation TVA post-comptabilisation")
    ap.add_argument("--dossier", required=True)
    ap.add_argument("--annee", required=True, type=int)
    ap.add_argument("--gl", help="export du grand livre (.xlsx ou .csv)")
    ap.add_argument("--feuille", help="nom de la feuille du GL (defaut : la premiere)")
    ap.add_argument("--sortie")
    a = ap.parse_args()
    racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    conf = charger_yaml(os.path.join(a.dossier, "societe.yaml"))
    mgl = charger_yaml(os.path.join(racine, "config", "mapping_gl.yaml"))
    profil = mgl["profils"][conf.get("profil_gl", "standard_fr")]

    out = os.path.abspath(a.sortie or os.path.join(a.dossier, "annexes"))
    lots = charger_declares(out, a.annee)
    if not lots:
        sys.exit("Aucune declaration periodique %s trouvee dans %s" % (a.annee, out))
    factures = cumuler(lots)
    anomalies = controler_cumul(lots, conf.get("periodicite"), a.annee) \
        + controler_collisions(factures)

    res = {"concordant": [], "ecart": [], "declare_non_compta": [], "compta_non_declare": []}
    if a.gl:
        pieces = charger_gl(a.gl, a.feuille, profil, mgl["selection"], a.annee)
        print("Grand livre : %d piece(s) d'achat/vente retenue(s) pour %s"
              % (len(pieces), a.annee))
        res = rapprocher(factures, pieces, mgl["tolerance_montant"], mgl["tolerance_jours"])

    base = os.path.join(out, "%s-RECONCILIATION-%s" % (conf["code"], a.annee))
    fx = generer_xlsx(base + ".xlsx", conf, a.annee, lots, anomalies, factures, res, bool(a.gl))
    ft = generer_txt(base + "-SYNTHESE.txt", conf, a.annee, lots, anomalies, factures,
                     res, bool(a.gl))
    print("Exercice %s : %d periode(s) declaree(s), %d facture(s) cumulee(s), "
          "%d anomalie(s) de cumul" % (a.annee, len(lots), len(factures), len(anomalies)))
    if a.gl:
        print("  concordants %d | ecarts %d | declare non compta %d | "
              "COMPTA NON DECLARE %d"
              % (len(res["concordant"]), len(res["ecart"]),
                 len(res["declare_non_compta"]), len(res["compta_non_declare"])))
        print("  TVA jamais declaree : %.2f"
              % sum(p["tva"] for p in res["compta_non_declare"]))
    for f in (fx, ft):
        print("  -> %s" % os.path.basename(f))

if __name__ == "__main__":
    main()
