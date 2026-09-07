#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Replique PDF de la declaration TVA -- document de preparation destine a etre
envoye au client pour accord avant depot.

NE RECALCULE RIEN. Le module recharge l'instantane <...>-DECLARE.json produit
par src/annexes_tva.py et rappelle la fonction d'agregation du moteur
lui-meme (annexes_tva.agreger) avec ces donnees : les montants affiches sont
donc exactement ceux du livrable, produits par le meme code, jamais par une
arithmetique refaite ici.

Ce document porte l'identite du cabinet (config/cabinet.yaml) et JAMAIS les
armoiries ou l'en-tete de l'Etat : ce n'est pas une copie du formulaire
administratif, c'est un document de travail qui en presente le contenu.

Le filigrane BROUILLON - NON DEPOSE est pose tant que la periode n'a pas ete
marquee comme deposee (dossiers/<CODE>/depots.json). Il n'existe volontairement
aucun parametre permettant de le retirer : seul le marquage de depot le leve.
"""
import datetime as dt
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "src"))
import annexes_tva as at  # noqa: E402  (sys.path modifie juste au-dessus)

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,  # noqa: E402
                                 PageTemplate, Paragraph, Spacer, Table,
                                 TableStyle)

MENTION = ("Document de préparation — reprend le contenu de la déclaration à "
           "déposer. N'a pas valeur de déclaration officielle.")
FILIGRANE = "BROUILLON — NON DÉPOSÉ"

# Decoupage du formulaire. Le total de section n'est jamais une addition faite
# ici : c'est la case que le formulaire designe lui-meme comme total, reprise
# telle quelle. On la designe par sa cle de poste (stable d'un profil a
# l'autre) plutot que par son numero de case (qui, lui, change entre DECA et
# DECM).
# La replique client ne porte QUE le solde. Le detail du chiffre d'affaires,
# de la taxe due et de la taxe deductible vit dans le livrable
# "declaration officielle + annexes", qui reprend le formulaire complet. Ce
# document-ci sert a une seule chose : faire dire oui ou non au client sur le
# montant a payer ou a recuperer.
SECTIONS = [
    ("IV", "Solde", ["solde"], ("solde", "excedent")),
]


# ---------------------------------------------------------------------
# Donnees
# ---------------------------------------------------------------------

def charger_cabinet(repo=REPO):
    p = os.path.join(repo, "config", "cabinet.yaml")
    if not os.path.isfile(p):
        return {}
    try:
        return at.charger_yaml(p) or {}
    except Exception:
        return {}


def chemin_depots(dossier_abs):
    return os.path.join(dossier_abs, "depots.json")


def lire_depots(dossier_abs):
    p = chemin_depots(dossier_abs)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def statut_depot(dossier_abs, periode):
    """Renvoie le dictionnaire de depot de la periode, ou None si non deposee."""
    return lire_depots(dossier_abs).get(periode)


def marquer_depose(dossier_abs, periode, reference=None, note=None):
    """Marque une periode comme deposee. Geste deliberé et trace : c'est la
    seule chose qui leve le filigrane des repliques regenerees ensuite."""
    depots = lire_depots(dossier_abs)
    depots[periode] = {
        "depose_le": dt.datetime.now().isoformat(timespec="seconds"),
        "reference_accuse": reference or None,
        "note": note or None,
    }
    with open(chemin_depots(dossier_abs), "w", encoding="utf-8") as f:
        json.dump(depots, f, ensure_ascii=False, indent=2, sort_keys=True)
    return depots[periode]


def annuler_depot(dossier_abs, periode):
    depots = lire_depots(dossier_abs)
    if periode in depots:
        del depots[periode]
        with open(chemin_depots(dossier_abs), "w", encoding="utf-8") as f:
            json.dump(depots, f, ensure_ascii=False, indent=2, sort_keys=True)
        return True
    return False


def dernier_declare(dossier_sortie):
    fichiers = sorted(glob.glob(os.path.join(dossier_sortie, "*-DECLARE.json")),
                       key=os.path.getmtime, reverse=True)
    if not fichiers:
        return None, None
    with open(fichiers[0], encoding="utf-8") as f:
        return json.load(f), fichiers[0]


def construire_postes(declare, repo=REPO):
    """Toutes les cases du profil, dans l'ordre du formulaire, avec le montant
    quand il existe et vide sinon -- le client doit voir qu'aucune case n'a
    ete oubliee. Les montants viennent de annexes_tva.agreger, jamais d'un
    calcul refait ici."""
    mapping = at.charger_yaml(os.path.join(repo, "config", "mapping_ecdf.yaml"))
    nom_profil, profil = at.choisir_profil(mapping, declare["periode"])
    agr = at.agreger(declare["lignes"], declare["prorata_pct"],
                     declare["origine_prorata"], profil,
                     declare.get("ca_annee_total"), declare.get("ca_annee_ouvrant_droit"),
                     declare.get("prorata_brut"))

    calcule = {(c["bloc"], c["poste"]): c for c in agr["cases"]}
    postes = {}
    for bloc, contenu in profil.items():
        if not isinstance(contenu, dict):
            continue
        for cle, m in contenu.items():
            if not isinstance(m, dict) or not ("base" in m or "taxe" in m):
                continue
            c = calcule.get((bloc, cle))
            postes.setdefault(bloc, []).append({
                "poste": cle, "libelle": m.get("libelle", cle),
                "case_base": m.get("base"), "case_taxe": m.get("taxe"),
                "base": c["base"] if c else None,
                "taxe": c["taxe"] if c else None,
                "renseigne": c is not None,
            })
    return nom_profil, profil, postes, agr


# ---------------------------------------------------------------------
# Mise en forme
# ---------------------------------------------------------------------

def fmt_montant(v):
    if v is None:
        return ""
    s = "%,.2f" % v if False else "{:,.2f}".format(v)
    return s.replace(",", " ").replace(".", ",")


def _styles():
    base = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle("titre", parent=base["Title"], fontSize=16,
                                 spaceAfter=2 * mm, alignment=TA_CENTER),
        "soustitre": ParagraphStyle("soustitre", parent=base["Normal"], fontSize=10.5,
                                     textColor=colors.HexColor("#444444"),
                                     alignment=TA_CENTER, spaceAfter=6 * mm),
        "mention": ParagraphStyle("mention", parent=base["Normal"], fontSize=9,
                                   textColor=colors.HexColor("#8a1f1f"),
                                   alignment=TA_CENTER, spaceAfter=5 * mm,
                                   borderPadding=3, leading=12),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontSize=12,
                                   spaceBefore=6 * mm, spaceAfter=2 * mm),
        "normal": base["Normal"],
        "petit": ParagraphStyle("petit", parent=base["Normal"], fontSize=8.5,
                                 textColor=colors.HexColor("#555555")),
        "cabinet": ParagraphStyle("cabinet", parent=base["Normal"], fontSize=10,
                                   leading=13),
        "libelle": ParagraphStyle("libelle", parent=base["Normal"], fontSize=8.5,
                                   leading=10.5),
        "cellule": ParagraphStyle("cellule", parent=base["Normal"], fontSize=9.5,
                                   leading=12),
        "solde": ParagraphStyle("solde", parent=base["Normal"], fontSize=17,
                                 leading=20, alignment=2),
    }


def _decor_page(filigrane, cabinet, nom_fichier):
    """Filigrane + pied de page, appliques a chaque page. Le filigrane n'a
    volontairement aucun interrupteur : il decoule du statut de depot."""

    def dessiner(canvas, doc):
        canvas.saveState()
        if filigrane:
            canvas.setFont("Helvetica-Bold", 46)
            canvas.setFillColor(colors.Color(0.85, 0.16, 0.16, alpha=0.13))
            canvas.translate(A4[0] / 2.0, A4[1] / 2.0)
            canvas.rotate(38)
            canvas.drawCentredString(0, 0, FILIGRANE)
            canvas.restoreState()
            canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        gauche = cabinet.get("nom") or ""
        if cabinet.get("mention_pied"):
            gauche = "%s — %s" % (gauche, cabinet["mention_pied"])
        canvas.drawString(18 * mm, 12 * mm, gauche[:110])
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm,
                                "Page %d" % canvas.getPageNumber())
        canvas.drawCentredString(A4[0] / 2.0, 8 * mm, nom_fichier[:90])
        canvas.restoreState()

    return dessiner


def _bloc_cabinet(cabinet, styles):
    lignes = [("<b>%s</b>" % cabinet.get("nom", "")).strip()]
    for x in (cabinet.get("adresse") or []):
        lignes.append(str(x))
    for x in (cabinet.get("contact") or []):
        lignes.append(str(x))
    return Paragraph("<br/>".join(l for l in lignes if l), styles["cabinet"])


def _tableau_identite(declare, nom_profil, profil, depot, conf_societe, styles):
    per = declare.get("periode_libelle") or declare.get("periode")
    origine = declare.get("origine_prorata") or ""
    statut_p = "DÉPOSÉE" if depot else "NON DÉPOSÉE — brouillon"
    if depot:
        statut_p += "  (le %s" % depot.get("depose_le", "")[:10]
        if depot.get("reference_accuse"):
            statut_p += ", accusé %s" % depot["reference_accuse"]
        statut_p += ")"
    # L'origine du prorata est une phrase entiere : elle doit s'envelopper,
    # pas deborder de la cellule.
    prorata = Paragraph("<b>%g %%</b><br/><font size=7.5 color='#555555'>%s</font>"
                        % (declare.get("prorata_pct", 0), origine), styles["cellule"])
    donnees = [
        ["Société", declare.get("denomination") or declare.get("code")],
        ["Matricule", conf_societe.get("matricule") or "—"],
        ["N° de TVA", conf_societe.get("numero_tva") or "—"],
        ["Période", per],
        ["Date de production", dt.datetime.now().strftime("%d/%m/%Y")],
        ["Prorata appliqué", prorata],
        ["Profil eCDF", "%s — statut %s" % (nom_profil, profil.get("statut", "?"))],
        ["Statut de la période", statut_p],
    ]
    t = Table(donnees, colWidths=[42 * mm, 130 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#dddddd")),
        ("TEXTCOLOR", (1, 7), (1, 7),
         colors.HexColor("#8a1f1f") if not depot else colors.HexColor("#1d6b2f")),
        ("FONTNAME", (1, 7), (1, 7), "Helvetica-Bold"),
    ]))
    return t


def _tableau_section(postes, styles):
    entete = ["Case", "Libellé officiel", "Base", "Case", "Taxe"]
    lignes = [entete]
    for p in postes:
        lignes.append([
            p["case_base"] or "",
            Paragraph(p["libelle"], styles["libelle"]),
            fmt_montant(p["base"]),
            p["case_taxe"] or "",
            fmt_montant(p["taxe"]),
        ])
    t = Table(lignes, colWidths=[13 * mm, 96 * mm, 24 * mm, 13 * mm, 24 * mm],
              repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0f3")),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("ALIGN", (4, 0), (4, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dbe0")),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
    ]
    # Les cases non renseignees restent visibles mais discretes : le client
    # doit voir qu'elles existent et qu'elles sont vides.
    for i, p in enumerate(postes, start=1):
        if not p["renseigne"]:
            style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#9aa0a6")))
    t.setStyle(TableStyle(style))
    return t


def _ligne_total(libelle, valeur):
    t = Table([[libelle, fmt_montant(valeur)]], colWidths=[122 * mm, 48 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f6f8")),
        ("LINEABOVE", (0, 0), (-1, 0), 0.75, colors.HexColor("#8a9099")),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
    ]))
    return t


def _encadre_solde(case, valeur, styles):
    """Solde final mis en evidence. La lecture 'a payer / en faveur de la
    societe' n'est qu'une lecture du signe de la case du formulaire, pas un
    calcul supplementaire."""
    if valeur is None:
        lecture = "—"
    elif valeur > 0:
        lecture = "TVA à payer"
    elif valeur < 0:
        lecture = "Excédent en faveur de la société"
    else:
        lecture = "Solde nul"
    t = Table([[Paragraph("<b>SOLDE DE LA PÉRIODE</b><br/>"
                           "<font size=8.5 color='#555555'>case %s du formulaire — %s</font>"
                           % (case or "—", lecture), styles["cellule"]),
                Paragraph("<b>%s</b>" % fmt_montant(valeur), styles["solde"])]],
              colWidths=[112 * mm, 58 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d7e4fa")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#ffffff")),
        ("BOX", (0, 0), (-1, 0), 2.0, colors.HexColor("#1f4685")),
        ("BOX", (1, 0), (1, 0), 1.1, colors.HexColor("#1f4685")),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 11),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 11),
        ("LEFTPADDING", (0, 0), (-1, 0), 10),
        ("RIGHTPADDING", (0, 0), (-1, 0), 10),
    ]))
    return t


def _tableau_non_mappes(non_mappes, styles):
    lignes = [["Poste", "Base", "Taxe"]]
    for poste, base, taxe in non_mappes:
        lignes.append([Paragraph(str(poste), styles["libelle"]),
                       fmt_montant(base), fmt_montant(taxe)])
    t = Table(lignes, colWidths=[122 * mm, 24 * mm, 24 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fff4e5")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6c9a0")),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
    ]))
    return t


# ---------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------

def nom_replique(declare):
    return "%s-DECLARATION-TVA-%s-%s-REPLIQUE.pdf" % (
        declare.get("code", "SOCIETE"), declare.get("periode", "PERIODE"),
        dt.date.today().strftime("%Y%m%d"))


def generer_replique(dossier_abs, dossier_sortie, repo=REPO, chemin_sortie=None):
    """Produit la replique PDF a partir du dernier instantane du dossier de
    sortie. Renvoie (chemin_pdf, infos)."""
    declare, chemin_declare = dernier_declare(dossier_sortie)
    if declare is None:
        raise FileNotFoundError("aucun instantané -DECLARE.json dans %s" % dossier_sortie)

    cabinet = charger_cabinet(repo)
    conf_societe = at.charger_yaml(os.path.join(dossier_abs, "societe.yaml")) or {}
    nom_profil, profil, postes, agr = construire_postes(declare, repo)
    depot = statut_depot(dossier_abs, declare["periode"])

    chemin = chemin_sortie or os.path.join(dossier_sortie, nom_replique(declare))
    styles = _styles()
    doc = BaseDocTemplate(chemin, pagesize=A4,
                           leftMargin=18 * mm, rightMargin=18 * mm,
                           topMargin=16 * mm, bottomMargin=18 * mm,
                           title="Déclaration TVA — document de préparation",
                           author=cabinet.get("nom") or "")
    cadre = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="corps")
    decor = _decor_page(depot is None, cabinet, os.path.basename(chemin))
    doc.addPageTemplates([PageTemplate(id="page", frames=[cadre], onPage=decor)])

    hist = []
    # -- page de garde -------------------------------------------------
    if cabinet.get("logo"):
        chemin_logo = cabinet["logo"]
        if not os.path.isabs(chemin_logo):
            chemin_logo = os.path.join(repo, chemin_logo)
        if os.path.isfile(chemin_logo):
            from reportlab.platypus import Image
            try:
                img = ImageReader(chemin_logo)
                lg, ht = img.getSize()
                largeur = 38 * mm
                hist.append(Image(chemin_logo, width=largeur,
                                  height=largeur * ht / float(lg)))
                hist.append(Spacer(1, 4 * mm))
            except Exception:
                pass
    hist.append(_bloc_cabinet(cabinet, styles))
    hist.append(Spacer(1, 10 * mm))
    hist.append(Paragraph("Déclaration TVA — contenu à déposer", styles["titre"]))
    hist.append(Paragraph(declare.get("periode_libelle") or declare.get("periode", ""),
                          styles["soustitre"]))
    hist.append(Paragraph(MENTION, styles["mention"]))
    hist.append(Spacer(1, 4 * mm))
    hist.append(_tableau_identite(declare, nom_profil, profil, depot, conf_societe,
                                   styles))
    hist.append(Spacer(1, 6 * mm))
    if declare.get("exceptions_bloquantes"):
        hist.append(Paragraph(
            "<b>%d exception(s) bloquante(s)</b> subsistent sur cette période : les "
            "lignes concernées sont exclues des montants ci-après. Voir le rapport "
            "d'exceptions joint." % declare["exceptions_bloquantes"], styles["mention"]))
    hist.append(Paragraph(
        "Ce document présente le <b>solde de la période</b> et lui seul : le montant "
        "à verser à l'Administration, ou l'excédent à récupérer. Il est destiné à "
        "recueillir votre accord sur ce montant avant tout dépôt.<br/><br/>"
        "Le détail complet de la déclaration — chiffre d'affaires, taxe due, taxe "
        "déductible, case par case — figure dans le document «\u00a0Déclaration "
        "officielle et annexes\u00a0», établi sur le formulaire %s. Les cases sans "
        "montant y sont affichées vides et non omises, afin que la totalité des "
        "postes puisse être passée en revue." % nom_profil,
        styles["petit"]))
    hist.append(Spacer(1, 4 * mm))

    # -- section IV : le solde -----------------------------------------
    for numero, titre, blocs, cle_total in SECTIONS:
        rangs = []
        for b in blocs:
            rangs.extend(postes.get(b, []))
        if not rangs:
            continue
        bloc_t, poste_t = cle_total
        ref = next((p for p in postes.get(bloc_t, []) if p["poste"] == poste_t), None)
        fin = []
        if ref is not None:
            valeur = ref["taxe"] if ref["taxe"] is not None else ref["base"]
            case = ref["case_taxe"] or ref["case_base"]
            if numero == "IV":
                fin = [Spacer(1, 3 * mm), _encadre_solde(case, valeur, styles)]
            else:
                fin = [_ligne_total("Total de la section", valeur)]
        titre_p = Paragraph(titre, styles["section"])
        tableau = _tableau_section(rangs, styles)
        if len(rangs) <= 12:
            # Section courte : titre, tableau et total restent sur une page.
            hist.append(KeepTogether([titre_p, tableau] + fin))
        else:
            # Section longue : le tableau peut se couper, mais son titre ne
            # doit pas rester seul en bas de page (d'ou le keepWithNext).
            titre_p.keepWithNext = True
            hist.append(titre_p)
            hist.append(tableau)
            hist.extend(fin)

    # -- postes sans case mappee ---------------------------------------
    bloc_nm = [Paragraph("Postes sans case mappée", styles["section"])]
    if agr["non_mappes"]:
        bloc_nm.append(Paragraph(
            "Ces montants ne correspondent à aucune case du profil retenu. La "
            "ventilation attendue est indiquée lorsqu'elle est connue ; elle relève "
            "d'une décision du préparateur, pas du calcul.", styles["petit"]))
        bloc_nm.append(Spacer(1, 2 * mm))
        bloc_nm.append(_tableau_non_mappes(agr["non_mappes"], styles))
    else:
        bloc_nm.append(Paragraph("Néant — tous les montants sont rattachés à une case.",
                                  styles["petit"]))
    hist.append(KeepTogether(bloc_nm))

    doc.build(hist)
    return chemin, {
        "declare": os.path.basename(chemin_declare),
        "profil": nom_profil,
        "statut_profil": profil.get("statut"),
        "depose": depot is not None,
        "depot": depot,
        "postes": sum(len(v) for v in postes.values()),
        "postes_renseignes": sum(1 for v in postes.values() for p in v if p["renseigne"]),
        "non_mappes": len(agr["non_mappes"]),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage : python3 replique_ecdf.py <dossiers/CODE> [dossier_sortie]")
    dossier = os.path.abspath(sys.argv[1])
    conf = at.charger_yaml(os.path.join(dossier, "societe.yaml")) or {}
    sortie = sys.argv[2] if len(sys.argv) > 2 else (
        conf.get("dossier_sortie") or os.path.join(dossier, "annexes"))
    chemin, infos = generer_replique(dossier, os.path.abspath(os.path.expanduser(sortie)))
    print("Réplique produite : %s" % chemin)
    for k, v in infos.items():
        print("  %-18s %s" % (k, v))
