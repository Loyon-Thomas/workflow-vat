#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Livrable "Declaration officielle + annexes" : le formulaire eCDF tel qu'il
est publie, rempli des montants de la periode, suivi des annexes detaillees.

DEUX PARTIES, DEUX NATURES
  1. Le formulaire officiel, page pour page, avec les montants ecrits en
     face de leur case. On ne redessine PAS le formulaire : on superpose du
     texte sur le PDF publie par l'administration. La mise en page, les
     libelles et les numeros de case restent donc ceux du document officiel,
     sans risque de transcription.
  2. Les annexes, transcrites depuis le classeur deja produit par le moteur.
     On relit le .xlsx, on ne recalcule rien.

AUCUN CALCUL ICI. Les montants viennent de annexes_tva.agreger, via
replique_ecdf.construire_postes, exactement comme la replique client.

Ce document n'est pas un depot : il porte le meme filigrane BROUILLON que la
replique tant que la periode n'est pas marquee deposee. Le PDF officiel qui
fait foi est celui que le portail eCDF genere lui-meme apres transfert.
"""
import io
import os
import sys

import fitz  # PyMuPDF : superposition de texte sur le PDF officiel
import openpyxl
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "src"))
import annexes_tva as at          # noqa: E402
import replique_ecdf as rep       # noqa: E402

# Formulaire officiel a utiliser selon le profil retenu par le moteur.
# Le mensuel et le trimestriel partagent le meme type eCDF (TVA_DECM) mais
# pas le meme document publie : l'un porte 12 periodes, l'autre 4.
FORMULAIRES = {
    ("TVA_DECM_2025", "M"): "TVA_DECM_mensuelle_2025.pdf",
    ("TVA_DECM_2025", "Q"): "TVA_DECM_trimestrielle_2025.pdf",
    ("TVA_DECA_2025", "A"): "TVA_DECA_annuelle_2025.pdf",
}

# Geometrie de la colonne des numeros de case, relevee sur les formulaires
# 2025 : le numero occupe x=469..477, le trait de saisie va de x=480 a 571.
# Les bornes sont larges a dessein -- il s'agit d'ecarter les numeros de case
# CITES DANS UN LIBELLE ("Total tax due (046+056+...)"), qui vivent, eux,
# dans la colonne de gauche.
COLONNE_CASE = (455.0, 495.0)
FIN_TRAIT = 571.0
MARGE_DROITE = 2.0


def _type_periode(periode):
    suffixe = periode[5:]
    if suffixe.startswith("M"):
        return "M"
    if suffixe.startswith("Q"):
        return "Q"
    return "A"


def formulaire_officiel(nom_profil, periode):
    """Chemin du PDF officiel correspondant, ou None si aucun ne convient."""
    nom = FORMULAIRES.get((nom_profil, _type_periode(periode)))
    if not nom:
        return None
    chemin = os.path.join(REPO, "formulaires-officiels", nom)
    return chemin if os.path.isfile(chemin) else None


def valeurs_par_case(postes):
    """{numero de case: montant}. Une case de base et une case de taxe sont
    deux cases distinctes du formulaire, chacune avec sa valeur."""
    valeurs = {}
    for rangs in postes.values():
        for p in rangs:
            if p.get("case_base") and p.get("base") is not None:
                valeurs[str(p["case_base"])] = p["base"]
            if p.get("case_taxe") and p.get("taxe") is not None:
                valeurs[str(p["case_taxe"])] = p["taxe"]
    return valeurs


def _cases_de_la_page(page):
    """Numeros de case reellement imprimes dans la colonne des cases.

    On filtre sur la position horizontale : un numero cite dans un libelle
    ("(046+056+407)") n'est pas une case a remplir, et le confondre avec une
    case ecrirait un montant au milieu d'une phrase.
    """
    trouves = []
    for x0, y0, x1, y1, mot, *_ in page.get_text("words"):
        if not (mot.isdigit() and len(mot) == 3):
            continue
        if not (COLONNE_CASE[0] <= x0 <= COLONNE_CASE[1]):
            continue
        trouves.append((mot, y0, y1))
    return trouves


def remplir_formulaire(chemin_pdf, valeurs, filigrane=None):
    """Superpose les montants sur le formulaire officiel. Renvoie les octets
    du PDF produit et le nombre de cases effectivement remplies."""
    doc = fitz.open(chemin_pdf)
    remplies = 0
    for page in doc:
        for numero, y0, y1 in _cases_de_la_page(page):
            if numero not in valeurs:
                continue
            texte = rep.fmt_montant(valeurs[numero])
            largeur = fitz.get_text_length(texte, fontname="hebo", fontsize=8.5)
            # Aligne a droite sur la fin du trait de saisie, pose juste
            # au-dessus de la ligne de base du numero de case.
            page.insert_text(
                fitz.Point(FIN_TRAIT - MARGE_DROITE - largeur, y1 - 0.5),
                texte, fontname="hebo", fontsize=8.5,
                color=(0.05, 0.11, 0.35))
            remplies += 1
        if filigrane:
            _poser_filigrane(page, filigrane)
    sortie = doc.tobytes()
    doc.close()
    return sortie, remplies


def _poser_filigrane(page, texte):
    """Meme mention que la replique client, en diagonale, en filet clair pour
    rester lisible par-dessus le formulaire."""
    r = page.rect
    point = fitz.Point(r.width * 0.13, r.height * 0.62)
    # insert_text n'accepte que 0/90/180/270 en rotate : pour une diagonale
    # il faut passer par morph, qui applique une matrice autour d'un pivot.
    page.insert_text(
        point, texte, fontname="hebo", fontsize=38,
        color=(0.85, 0.24, 0.24), fill_opacity=0.16,
        morph=(point, fitz.Matrix(45)))


# ---------------------------------------------------------------------
# Annexes : transcription du classeur deja produit
# ---------------------------------------------------------------------

def _styles_annexes():
    base = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle("titre", parent=base["Normal"], fontName="Helvetica-Bold",
                                 fontSize=12, spaceAfter=6),
        "onglet": ParagraphStyle("onglet", parent=base["Normal"], fontName="Helvetica-Bold",
                                  fontSize=9.5, textColor=colors.HexColor("#1f4685"),
                                  spaceBefore=10, spaceAfter=4),
        "cellule": ParagraphStyle("cellule", parent=base["Normal"], fontSize=6.6,
                                   leading=8),
        "petit": ParagraphStyle("petit", parent=base["Normal"], fontSize=8,
                                 textColor=colors.HexColor("#555555")),
    }


def _valeur_cellule(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return rep.fmt_montant(v)
    return str(v)


def annexes_en_pdf(chemin_xlsx, titre, styles=None):
    """Transcrit le classeur en pages PDF, un tableau par onglet.

    data_only=True : les valeurs lues sont celles que le moteur a ecrites,
    jamais des formules reevaluees ici.
    """
    styles = styles or _styles_annexes()
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=landscape(A4),
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=titre)
    largeur_utile = landscape(A4)[0] - 20 * mm

    hist = [Paragraph("Annexes détaillées", styles["titre"]),
            Paragraph("Transcription du classeur %s. Les montants sont ceux "
                      "produits par le moteur ; rien n'est recalculé ici."
                      % os.path.basename(chemin_xlsx), styles["petit"])]

    classeur = openpyxl.load_workbook(chemin_xlsx, data_only=True, read_only=True)
    for onglet in classeur.worksheets:
        lignes = [l for l in onglet.iter_rows(values_only=True)
                  if any(c is not None and str(c).strip() for c in l)]
        hist.append(Paragraph(onglet.title, styles["onglet"]))
        if not lignes:
            hist.append(Paragraph("Onglet vide.", styles["petit"]))
            continue
        n_col = max(len(l) for l in lignes)
        donnees = [[Paragraph(_valeur_cellule(c), styles["cellule"])
                    for c in list(l) + [None] * (n_col - len(l))] for l in lignes]
        t = Table(donnees, colWidths=[largeur_utile / n_col] * n_col, repeatRows=0)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dcdfe5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        hist.append(t)
        hist.append(Spacer(1, 3 * mm))
    classeur.close()

    doc.build(hist)
    return tampon.getvalue()


# ---------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------

def nom_document(declare):
    return "%s-DECLARATION-OFFICIELLE-%s-%s.pdf" % (
        declare.get("code") or "SOCIETE", declare["periode"],
        (declare.get("genere_le") or "")[:10].replace("-", "") or "SANSDATE")


def generer(dossier_abs, dossier_sortie, repo=REPO):
    """Produit le document complet. Renvoie (chemin, infos)."""
    declare, chemin_declare = rep.dernier_declare(dossier_sortie)
    if declare is None:
        raise FileNotFoundError("aucun instantané -DECLARE.json dans %s" % dossier_sortie)

    nom_profil, profil, postes, agr = rep.construire_postes(declare, repo)
    officiel = formulaire_officiel(nom_profil, declare["periode"])
    if officiel is None:
        raise FileNotFoundError(
            "aucun formulaire officiel pour le profil %s en période %s — "
            "attendu dans formulaires-officiels/" % (nom_profil, declare["periode"]))

    depot = rep.statut_depot(dossier_abs, declare["periode"])
    valeurs = valeurs_par_case(postes)
    pdf_formulaire, remplies = remplir_formulaire(
        officiel, valeurs, filigrane=None if depot else rep.FILIGRANE)

    # Le classeur de la meme periode, a cote de l'instantane.
    base = os.path.basename(chemin_declare)[:-len("-DECLARE.json")]
    chemin_xlsx = os.path.join(dossier_sortie, base + ".xlsx")
    morceaux = [pdf_formulaire]
    if os.path.isfile(chemin_xlsx):
        morceaux.append(annexes_en_pdf(chemin_xlsx, nom_document(declare)))

    final = fitz.open()
    for octets in morceaux:
        part = fitz.open("pdf", octets)
        final.insert_pdf(part)
        part.close()
    chemin = os.path.join(dossier_sortie, nom_document(declare))
    final.save(chemin)
    pages = final.page_count
    final.close()

    return chemin, {
        "declare": os.path.basename(chemin_declare),
        "formulaire": os.path.basename(officiel),
        "profil": nom_profil,
        "cases_remplies": remplies,
        "cases_disponibles": len(valeurs),
        "annexes_jointes": os.path.isfile(chemin_xlsx),
        "pages": pages,
        "depose": depot is not None,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage : python3 declaration_officielle.py <dossiers/CODE> <dossier_sortie>")
    chemin, infos = generer(os.path.abspath(sys.argv[1]),
                             os.path.abspath(os.path.expanduser(sys.argv[2])))
    print("Document produit : %s" % chemin)
    for k, v in infos.items():
        print("  %-20s %s" % (k, v))
