#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fichier XML eCDF deposable sur la plateforme, pour l'administration TVA.

NE RECALCULE RIEN : comme replique_ecdf.py, ce module recharge l'instantane
<...>-DECLARE.json et rappelle annexes_tva.agreger. Les montants ecrits dans
le XML sont ceux du livrable, produits par le moteur.

AUCUN DEPOT AUTOMATIQUE. Ce module ecrit un fichier sur le disque, rien de
plus : le transfert vers eCDF reste un geste manuel.

TROIS CONTROLES avant qu'un fichier soit propose :
  1. validation contre le XSD (reconstruit -- voir config/ecdf/*.xsd) ;
  2. relecture des montants depuis le XML produit et recomparaison a ceux
     de l'annexe ; tout ecart bloque ;
  3. bornes de Period propres au type de declaration, que le XSD ne peut
     pas exprimer.
Si un controle echoue, aucun fichier n'est laisse dans le dossier de sortie.

NOM DU FICHIER : impose par eCDF (la reference du fichier + .xml). La
tracabilite societe / periode / version est portee par le journal
ecdf-envois.json ecrit a cote, pas par le nom.
"""
import datetime as dt
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from decimal import Decimal, ROUND_HALF_UP

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "src"))
import annexes_tva as at  # noqa: E402

NS = "http://www.ctie.etat.lu/2011/ecdf"
Q = "{%s}" % NS
XSD = os.path.join(REPO, "config", "ecdf", "eCDF_file_v2.0-reconstruit.xsd")
VERSION_FICHIER = "2.0"

# Type de declaration eCDF selon le suffixe de periode. Valeurs relevees dans
# la table officielle de la documentation XML v2.0 (section 4.3.9) : le
# trimestriel a son propre type, TVA_DECT, distinct du mensuel.
TYPES = {"M": "TVA_DECM", "Q": "TVA_DECT", "ANNUAL": "TVA_DECA"}
BORNES_PERIODE = {"TVA_DECM": (1, 12), "TVA_DECT": (1, 4), "TVA_DECA": (1, 1)}


class ControleEchoue(Exception):
    """Un des controles obligatoires a echoue : aucun fichier n'est propose."""


# ---------------------------------------------------------------------
# Conversion des montants -- le point sensible
# ---------------------------------------------------------------------

def fmt_ecdf(valeur):
    """Flottant Python -> chaine acceptee par eCDF.

    Virgule comme seul separateur decimal, deux decimales au maximum, aucun
    separateur de milliers, pas d'espace apres le signe negatif. La conversion
    passe par Decimal(str(...)) : convertir directement un flottant binaire
    produirait des valeurs comme 0,3000000000000000444.
    """
    if valeur is None:
        return None
    d = Decimal(str(valeur)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if d == 0:
        d = Decimal("0.00")  # evite un "-0,00" quand l'arrondi vient du bas
    return format(d, "f").replace(".", ",")


def lire_ecdf(texte):
    """Inverse de fmt_ecdf, pour la relecture de controle."""
    return Decimal(texte.replace(",", "."))


# ---------------------------------------------------------------------
# Identifiants et parametres
# ---------------------------------------------------------------------

def charger_config(repo=REPO):
    p = os.path.join(repo, "config", "ecdf.yaml")
    if not os.path.isfile(p):
        raise ControleEchoue("config/ecdf.yaml absent")
    return at.charger_yaml(p) or {}


def normaliser_matricule(v):
    n = re.sub(r"\D", "", str(v or ""))
    return n if len(n) in (11, 13) else None


def normaliser_tva(v):
    if not v or str(v).strip().upper() == "NE":
        return "NE"
    n = re.sub(r"\D", "", str(v))
    return n if len(n) == 8 else None


def normaliser_rcs(v):
    if not v or str(v).strip().upper() == "NE":
        return "NE"
    n = re.sub(r"\s", "", str(v)).upper()
    # Premiere lettre majuscule, non suivie immediatement d'un zero.
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,6}", n) or n[1:2] == "0":
        return None
    return n


def identifiants(source, role):
    """Triplet MatrNbr / RCSNbr / VATNbr normalise, ou une erreur explicite."""
    matr = normaliser_matricule(source.get("matricule"))
    rcs = normaliser_rcs(source.get("rcs"))
    tva = normaliser_tva(source.get("tva"))
    manque = []
    if matr is None:
        manque.append("matricule (11 ou 13 chiffres) : %r" % source.get("matricule"))
    if rcs is None:
        manque.append("RCS (ex. B123456, ou NE) : %r" % source.get("rcs"))
    if tva is None:
        manque.append("n° TVA (8 chiffres sans LU, ou NE) : %r" % source.get("tva"))
    if manque:
        raise ControleEchoue("Identifiants %s inutilisables — %s"
                              % (role, " ; ".join(manque)))
    return {"MatrNbr": matr, "RCSNbr": rcs, "VATNbr": tva}


def type_et_periode(periode):
    """'2025-Q3' -> ('TVA_DECT', 2025, 3)."""
    annee, suffixe = periode[:4], periode[5:]
    if suffixe == "ANNUAL":
        return TYPES["ANNUAL"], annee, 1
    if suffixe.startswith("M"):
        return TYPES["M"], annee, int(suffixe[1:])
    if suffixe.startswith("Q"):
        return TYPES["Q"], annee, int(suffixe[1:])
    raise ControleEchoue("periode non reconnue : %s" % periode)


def reference_fichier(prefixe, quand=None, sequence=1):
    quand = quand or dt.datetime.now()
    return "%sX%sT%s%02d" % (prefixe, quand.strftime("%Y%m%d"),
                              quand.strftime("%H%M%S"), sequence)


# ---------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------

def champs_declaration(declare, repo=REPO):
    """Liste ordonnee de (genre, id, valeur_texte) pour FormData.

    Contrairement a la replique PDF, une case sans montant est ICI omise :
    eCDF distingue explicitement un NumericField a 0 (valeur declaree) d'un
    element absent (case non renseignee), et interdit les elements vides.
    """
    mapping = at.charger_yaml(os.path.join(repo, "config", "mapping_ecdf.yaml"))
    nom_profil, profil = at.choisir_profil(mapping, declare["periode"])
    agr = at.agreger(declare["lignes"], declare["prorata_pct"],
                     declare["origine_prorata"], profil,
                     declare.get("ca_annee_total"),
                     declare.get("ca_annee_ouvrant_droit"),
                     declare.get("prorata_brut"))

    champs, attendus = [], {}
    for c in agr["cases"]:
        for case, valeur in ((c.get("case_base"), c.get("base")),
                             (c.get("case_taxe"), c.get("taxe"))):
            if case and valeur is not None:
                texte = fmt_ecdf(valeur)
                champs.append(("NumericField", case, texte))
                attendus[case] = (valeur, texte)
    return nom_profil, profil, agr, champs, attendus


def construire_arbre(declare, conf_societe, cfg, champs, reference):
    type_decl, annee, periode_num = type_et_periode(declare["periode"])
    bas, haut = BORNES_PERIODE[type_decl]
    if not bas <= periode_num <= haut:
        raise ControleEchoue("Period %d hors bornes pour %s (attendu %d a %d)"
                              % (periode_num, type_decl, bas, haut))

    agent = identifiants(cfg.get("agent") or {}, "de l'agent (config/ecdf.yaml)")
    declarant = identifiants({"matricule": conf_societe.get("matricule"),
                              "rcs": conf_societe.get("rcs"),
                              "tva": conf_societe.get("numero_tva")},
                             "du declarant (societe.yaml)")

    ET.register_namespace("", NS)
    racine = ET.Element(Q + "eCDFDeclarations")
    ET.SubElement(racine, Q + "FileReference").text = reference
    ET.SubElement(racine, Q + "eCDFFileVersion").text = VERSION_FICHIER
    ET.SubElement(racine, Q + "Interface").text = str(cfg.get("interface") or "")
    bloc_agent = ET.SubElement(racine, Q + "Agent")
    for cle in ("MatrNbr", "RCSNbr", "VATNbr"):
        ET.SubElement(bloc_agent, Q + cle).text = agent[cle]

    decls = ET.SubElement(racine, Q + "Declarations")
    declarer = ET.SubElement(decls, Q + "Declarer")
    for cle in ("MatrNbr", "RCSNbr", "VATNbr"):
        ET.SubElement(declarer, Q + cle).text = declarant[cle]

    decl = ET.SubElement(declarer, Q + "Declaration", {
        "type": type_decl, "model": str(cfg.get("model") or "1"),
        "language": str(cfg.get("langue") or "FR")})
    ET.SubElement(decl, Q + "Year").text = annee
    ET.SubElement(decl, Q + "Period").text = str(periode_num)
    form = ET.SubElement(decl, Q + "FormData")

    # Regime d'imposition : case 204 (debits) / 205 (recettes). Regle figee du
    # moteur, pas un choix fait ici. Present sur DECM, DECT et DECA.
    if type_decl in ("TVA_DECM", "TVA_DECT", "TVA_DECA"):
        debits = bool(cfg.get("regime_debits", True))
        ET.SubElement(form, Q + "Choice", {"id": "204"}).text = "1" if debits else "0"
        ET.SubElement(form, Q + "Choice", {"id": "205"}).text = "0" if debits else "1"

    for genre, case, texte in champs:
        ET.SubElement(form, Q + genre, {"id": case}).text = texte

    return racine, type_decl, annee, periode_num


def indenter(elem, niveau=0):
    marge = "\n" + "  " * niveau
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = marge + "  "
        for enfant in elem:
            indenter(enfant, niveau + 1)
        if not (elem.tail or "").strip():
            elem.tail = marge
        if not (elem[-1].tail or "").strip():
            elem[-1].tail = marge
    elif niveau and not (elem.tail or "").strip():
        elem.tail = marge


def serialiser(racine):
    indenter(racine)
    corps = ET.tostring(racine, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + corps + "\n"


# ---------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------

def valider_xsd(texte_xml, chemin_xsd=XSD):
    """Controle 1. Renvoie la liste des erreurs (vide si valide)."""
    try:
        import xmlschema
    except ImportError:
        raise ControleEchoue("le paquet xmlschema est requis pour valider "
                              "(pip3 install xmlschema)")
    schema = xmlschema.XMLSchema(chemin_xsd)
    return ["%s : %s" % (e.path or "?", (str(e.reason) or "")[:200])
            for e in schema.iter_errors(ET.fromstring(texte_xml))]


def controle_coherence(texte_xml, attendus):
    """Controle 2. Relit les montants DEPUIS le XML produit et les recompare a
    ceux de l'annexe. Un ecart, une case en trop ou une case manquante bloque."""
    racine = ET.fromstring(texte_xml)
    relus = {}
    for champ in racine.iter(Q + "NumericField"):
        relus[champ.get("id")] = lire_ecdf(champ.text)

    ecarts = []
    for case, (valeur, _texte) in sorted(attendus.items()):
        if case not in relus:
            ecarts.append("case %s absente du XML" % case)
            continue
        attendu = Decimal(str(valeur)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if relus[case] != attendu:
            ecarts.append("case %s : XML %s != annexe %s"
                          % (case, relus[case], attendu))
    for case in sorted(set(relus) - set(attendus)):
        ecarts.append("case %s presente dans le XML sans correspondance annexe" % case)
    return ecarts


# ---------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------

def journal_envois(dossier_sortie):
    return os.path.join(dossier_sortie, "ecdf-envois.json")


def inscrire_journal(dossier_sortie, entree):
    p = journal_envois(dossier_sortie)
    envois = []
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                envois = json.load(f)
        except Exception:
            envois = []
    envois.append(entree)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(envois, f, ensure_ascii=False, indent=2)
    return p


def generer_xml(dossier_abs, dossier_sortie, repo=REPO, declare=None):
    """Produit le XML eCDF. Ne laisse un fichier dans le dossier de sortie que
    si TOUS les controles passent. Renvoie (chemin, infos)."""
    if declare is None:
        import replique_ecdf
        declare, _ = replique_ecdf.dernier_declare(dossier_sortie)
    if declare is None:
        raise ControleEchoue("aucun instantané -DECLARE.json dans %s" % dossier_sortie)

    cfg = charger_config(repo)
    conf_societe = at.charger_yaml(os.path.join(dossier_abs, "societe.yaml")) or {}
    nom_profil, profil, agr, champs, attendus = champs_declaration(declare, repo)

    # Reference unique : on incremente la sequence tant qu'un fichier du meme
    # nom existe deja dans le dossier de sortie.
    quand = dt.datetime.now()
    prefixe = str(cfg.get("prefixe_agent") or "000000")
    for sequence in range(1, 100):
        reference = reference_fichier(prefixe, quand, sequence)
        chemin = os.path.join(dossier_sortie, reference + ".xml")
        if not os.path.exists(chemin):
            break
    else:
        raise ControleEchoue("99 fichiers deja produits dans la meme seconde")

    racine, type_decl, annee, periode_num = construire_arbre(
        declare, conf_societe, cfg, champs, reference)
    texte = serialiser(racine)

    erreurs_xsd = valider_xsd(texte)
    ecarts = controle_coherence(texte, attendus)
    if erreurs_xsd or ecarts:
        raise ControleEchoue(
            "XML non produit — %d erreur(s) de schéma, %d écart(s) de montant.\n%s"
            % (len(erreurs_xsd), len(ecarts),
               "\n".join(["  [schéma] " + e for e in erreurs_xsd[:5]]
                         + ["  [écart] " + e for e in ecarts[:5]])))

    # Ecriture atomique : le fichier n'apparait qu'une fois les controles passes.
    fd, provisoire = tempfile.mkstemp(dir=dossier_sortie, suffix=".xml.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texte)
    os.replace(provisoire, chemin)

    infos = {
        "fichier": os.path.basename(chemin),
        "reference": reference,
        "societe": declare.get("code"),
        "denomination": declare.get("denomination"),
        "periode": declare.get("periode"),
        "periode_libelle": declare.get("periode_libelle"),
        "type_declaration": type_decl,
        "model": str(cfg.get("model") or "1"),
        "annee": annee,
        "period": periode_num,
        "profil_mapping": nom_profil,
        "cases": len(champs),
        "produit_le": quand.isoformat(timespec="seconds"),
        "instantane": declare.get("genere_le"),
        "exceptions_bloquantes": declare.get("exceptions_bloquantes"),
        "interface": str(cfg.get("interface") or ""),
        "prefixe_agent": prefixe,
        "pret_pour_depot_reel": (str(cfg.get("interface") or "") not in ("", "IIIII")
                                  and prefixe != "000000"),
        "schema": "reconstruit — a confirmer par le portail eCDF",
    }
    inscrire_journal(dossier_sortie, infos)
    return chemin, infos


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage : python3 ecdf_xml.py <dossiers/CODE> [dossier_sortie]")
    dossier = os.path.abspath(sys.argv[1])
    conf = at.charger_yaml(os.path.join(dossier, "societe.yaml")) or {}
    sortie = sys.argv[2] if len(sys.argv) > 2 else (
        conf.get("dossier_sortie") or os.path.join(dossier, "annexes"))
    try:
        chemin, infos = generer_xml(dossier, os.path.abspath(os.path.expanduser(sortie)))
    except ControleEchoue as e:
        sys.exit("CONTROLE ECHOUE\n%s" % e)
    print("XML produit : %s" % chemin)
    for k, v in infos.items():
        print("  %-22s %s" % (k, v))
