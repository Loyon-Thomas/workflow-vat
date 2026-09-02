#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jeu d'essai fictif 2025 - TESTCO S.a r.l.
Genere : factures PDF, extractions JSON simulees, export comptable CSV.

AVERTISSEMENT : donnees entierement FICTIVES, destinees au test des modules.
Les extractions JSON sont produites par ce script, pas par une lecture de
document : le jeu teste la chaine de controle et d'agregation, pas la qualite
de l'extraction.
"""
import csv, json, os, shutil, sys, datetime as dt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

RACINE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(RACINE, "dossier-client")
DOSSIER = os.path.join(RACINE, "travail")
TVA_SOC = "LU12345678"
SOC = "TESTCO S.a r.l."

# ref, date, tiers, pays, regime, taux, base, tva, description
ACHATS = [
 ("A2501","2025-01-15","Bureau Lux SA","LU","achat_lu",0.17,2000.00,340.00,"Office supplies"),
 ("A2502","2025-01-28","Resto Lux Sarl","LU","achat_lu",0.03,450.00,13.50,"Catering"),
 ("A2503","2025-02-10","Immo Faiencerie SA","LU","exonere_art44",0.00,1800.00,0.00,"Office rent"),
 ("A2504","2025-02-20","Gaz et Elec Lux","LU","achat_lu",0.08,1200.00,96.00,"Utilities"),
 ("A2505","2025-03-05","Hotel Kirchberg","LU","achat_lu",0.14,800.00,112.00,"Accommodation"),
 ("A2506","2025-03-18","Software GmbH","DE","service_autoliq",0.17,3000.00,510.00,"Software licences"),
 ("A2507","2025-03-25","Assurances Lux SA","LU","exonere_art44",0.00,620.00,0.00,"Insurance"),
 ("A2508","2025-04-08","Mobilier Belge SA","BE","acq_intracom",0.17,4500.00,765.00,"Office furniture"),
 ("A2509","2025-04-22","Cloud Corp","US","service_autoliq_tiers",0.17,2400.00,408.00,"Cloud services"),
 ("A2510","2025-05-14","Bureau Lux SA","LU","achat_lu",0.17,1500.00,255.00,"Office supplies"),
 ("A2511","2025-05-30","Import CN Ltd","CN","import",0.17,5200.00,884.00,"Imported equipment"),
 ("A2512","2025-06-11","Telecom Lux SA","LU","achat_lu",0.17,240.00,40.80,"Phone costs"),
 ("A2513","2025-07-09","Conseil FR SARL","FR","service_autoliq",0.17,6000.00,1020.00,"Consulting services"),
 ("A2514","2025-08-19","Librairie Lux","LU","achat_lu",0.03,300.00,9.00,"Books and publications"),
 ("A2515","2025-09-02","Materiel NL BV","NL","acq_intracom",0.17,2800.00,476.00,"IT hardware"),
 ("A2516","2025-10-15","Bureau Lux SA","LU","achat_lu",0.17,1750.00,297.50,"Office supplies"),
 ("A2517","2025-11-20","Restaurant Lux","LU","achat_lu",0.14,640.00,89.60,"Business meals"),
 ("A2518","2025-12-10","Audit Lux SA","LU","achat_lu",0.17,9500.00,1615.00,"Audit fees"),
]
VENTES = [
 ("V2501","2025-01-20","Client Lux One SA","LU","vente_lu",0.17,25000.00,4250.00,"Administrative services"),
 ("V2502","2025-02-14","Client Lux Two Sarl","LU","vente_lu",0.03,5000.00,150.00,"Publications"),
 ("V2503","2025-03-28","Kunde DE GmbH","DE","livraison_intracom",0.00,40000.00,0.00,"Intra-community supply"),
 ("V2504","2025-04-15","Client US Inc","US","export",0.00,30000.00,0.00,"Export of goods"),
 ("V2505","2025-05-20","Client Lux Three SA","LU","vente_lu",0.08,8000.00,640.00,"Technical services"),
 ("V2506","2025-06-27","Client UK Ltd","UK","hors_ue",0.00,35000.00,0.00,"Advisory services fee"),
 ("V2507","2025-06-30","Groupe Lux entities","LU","exonere_art44",0.00,60000.00,0.00,"Sub-lease services"),
 ("V2508","2025-07-18","Client Lux One SA","LU","vente_lu",0.17,25000.00,4250.00,"Administrative services"),
 ("V2509","2025-08-29","Client FR SAS","FR","livraison_intracom",0.00,22000.00,0.00,"Intra-community supply"),
 ("V2510","2025-09-30","Client Lux Four SA","LU","vente_lu",0.14,6000.00,840.00,"Hospitality services"),
 ("V2511","2025-10-24","Client CH SA","CH","hors_ue",0.00,18000.00,0.00,"Advisory services fee"),
 ("V2512","2025-11-28","Client Lux One SA","LU","vente_lu",0.17,25000.00,4250.00,"Administrative services"),
 ("V2513","2025-12-19","Client JP KK","JP","export",0.00,12000.00,0.00,"Export of goods"),
]
AUTOLIQ = ("acq_intracom", "service_autoliq", "service_autoliq_tiers", "import")

def trimestre(d):
    return "2025-Q%d" % ((int(d[5:7]) - 1) // 3 + 1)

def pdf(chemin, ref, date, tiers, pays, regime, taux, base, tva, desc, sens, scan=False):
    c = canvas.Canvas(chemin, pagesize=A4)
    if scan:                                    # page sans couche texte -> "scan"
        c.setFillGray(0.92); c.rect(60, 640, 480, 150, fill=1, stroke=0)
        c.setFillGray(0.55)
        for i, y in enumerate(range(760, 640, -18)):
            c.rect(80, y, 320 - i * 22, 6, fill=1, stroke=0)
        c.setFillGray(0.75); c.rect(80, 560, 440, 3, fill=1, stroke=0)
        c.showPage(); c.save(); return
    em, de = (tiers, SOC) if sens == "achat" else (SOC, tiers)
    t = c.beginText(60, 780); t.setFont("Helvetica-Bold", 14); t.textLine(em)
    t.setFont("Helvetica", 9)
    t.textLine("VAT ID: %s" % (TVA_SOC if sens == "vente" else "LU00000000"))
    t.textLine(""); t.textLine("Bill to:"); t.setFont("Helvetica-Bold", 10)
    t.textLine(de); t.setFont("Helvetica", 9)
    t.textLine("VAT ID: %s" % (TVA_SOC if sens == "achat" else "LU00000000"))
    t.textLine(""); t.setFont("Helvetica-Bold", 12)
    t.textLine("INVOICE  %s" % ref); t.setFont("Helvetica", 10)
    t.textLine("Date: %s" % date); t.textLine("Country: %s" % pays); t.textLine("")
    t.textLine("Description: %s" % desc)
    t.textLine("Net amount:  %12.2f EUR" % base)
    if regime in AUTOLIQ:
        t.textLine("VAT:                 0.00 EUR - reverse charge")
        t.textLine("Total:       %12.2f EUR" % base)
        t.textLine("")
        t.textLine("VAT reverse charged by the customer (art. 196 / art. 17 par. 2).")
    elif regime == "exonere_art44":
        t.textLine("VAT:                 0.00 EUR")
        t.textLine("Total:       %12.2f EUR" % base)
        t.textLine(""); t.textLine("Exempt from VAT under article 44 of the Luxembourg VAT law.")
    else:
        t.textLine("VAT %5.2f%%:   %12.2f EUR" % (taux * 100, tva))
        t.textLine("Total:       %12.2f EUR" % (base + tva))
    c.drawText(t); c.showPage(); c.save()

def extraction(ref, date, tiers, pays, regime, taux, base, tva, desc, sens):
    em, de = (tiers, SOC) if sens == "achat" else (SOC, tiers)
    return {"sens": sens, "emetteur": em,
            "emetteur_tva": None if sens == "achat" else TVA_SOC,
            "destinataire": de,
            "destinataire_tva": TVA_SOC if sens == "achat" else None,
            "tiers": tiers, "pays": pays, "num_facture": ref, "date": date,
            "devise": "EUR", "taux_change": 1.0,
            "total_ttc": base if regime in AUTOLIQ or taux == 0 else round(base + tva, 2),
            "regime": regime, "source_extraction": "simule",
            "objet": desc,
            "lignes": [{"taux": taux, "base": base,
                        "tva": tva if regime in AUTOLIQ else (tva if taux else 0.0),
                        "description": desc}]}

def main():
    for d in (CLIENT, DOSSIER):
        os.makedirs(d, exist_ok=True)
    index = {}
    for lot, sens in ((ACHATS, "achat"), (VENTES, "vente")):
        for i, f in enumerate(lot):
            ref, date = f[0], f[1]
            rep = os.path.join(CLIENT, "Achats" if sens == "achat" else "Ventes", trimestre(date))
            os.makedirs(rep, exist_ok=True)
            nom = "%s %s.pdf" % (date, ref)
            chemin = os.path.join(rep, nom)
            scan = ref in ("A2509", "A2517", "V2510")     # 3 pieces sans couche texte
            pdf(chemin, *f, sens=sens, scan=scan)
            if ref in ("A2501", "V2503"):
                # copie a l'octet pres : ecartee par empreinte SHA-256
                shutil.copyfile(chemin, os.path.join(rep, nom.replace(".pdf", " (copie).pdf")))
            if ref == "A2512":
                # meme facture, fichier regenere : octets differents, l'empreinte
                # ne la voit pas. C'est le controle C6 qui doit l'attraper.
                c2 = os.path.join(rep, nom.replace(".pdf", " (rescan).pdf"))
                pdf(c2, *f, sens=sens, scan=False)
                index[c2] = extraction(*f, sens=sens)
            index[chemin] = extraction(*f, sens=sens)
    json.dump(index, open(os.path.join(RACINE, "_extractions_source.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=2)

    # --- export comptable, avec defauts volontaires --------------------
    with open(os.path.join(RACINE, "GL-2025.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Compte general", "Journal", "N° document", "Date document",
                    "Commentaire", "Base HTVA", "Montant TVA", "Code devise"])
        n = 0
        for lot, sens in ((ACHATS, "achat"), (VENTES, "vente")):
            for ref, date, tiers, pays, regime, taux, base, tva, desc in lot:
                if ref == "A2507":                        # DEFAUT 1 : non comptabilisee
                    continue
                if ref == "A2516":                        # DEFAUT 2 : ecart de montant
                    base, tva = 1700.00, 289.00
                n += 1
                w.writerow(["61000000" if sens == "achat" else "70000000",
                            "ACH" if sens == "achat" else "VEN",
                            "2025%04d" % n, date, "%s facture %s - %s" % (tiers, ref, desc),
                            base, tva, "EUR"])
        for ref, date, tiers, base, tva, sens in [                # DEFAUT 3 : jamais declarees
                ("A2519", "2025-11-05", "Fournisseur Oublie Sarl", 780.00, 132.60, "achat"),
                ("V2514", "2025-12-28", "Client Lux Five SA", 9000.00, 1530.00, "vente")]:
            n += 1
            w.writerow(["61000000" if sens == "achat" else "70000000",
                        "ACH" if sens == "achat" else "VEN", "2025%04d" % n, date,
                        "%s facture %s" % (tiers, ref), base, tva, "EUR"])
    print("Jeu 2025 genere : %d achats, %d ventes, 2 doublons, 3 scans" % (len(ACHATS), len(VENTES)))
    print("Defauts GL : A2507 non comptabilisee | A2516 ecart -50.00/-8.50 | "
          "A2519 et V2514 jamais declarees")

if __name__ == "__main__":
    main()
