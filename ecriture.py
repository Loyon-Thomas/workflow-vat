# -*- coding: utf-8 -*-
"""
Ecriture des fichiers d'etat, et verrou par societe.

ATOMIQUE. Un `open(p, "w")` vide le fichier AVANT d'y ecrire : un arret du
serveur, un disque plein ou une exception entre les deux laisse un fichier
tronque -- et les lecteurs qui avalaient l'erreur le prenaient ensuite pour
une liste vide. Les saisies manuelles, les ecartes ou le journal des envois
disparaissaient ainsi sans un mot.
Ici on ecrit dans un fichier provisoire du MEME dossier (meme volume, sans
quoi os.replace ne serait plus atomique), on force l'ecriture sur disque,
puis on substitue. Un lecteur voit l'ancien contenu ou le nouveau, jamais
un melange.

VERROU. _inventaire.json est lu-modifie-reecrit par le thread de lecture
(OCR), par l'inventaire, par la saisie manuelle et par la reintegration.
Deux de ces sequences entrelacees perdent l'une des deux modifications.
Un verrou par societe les rend sequentielles. Il est reentrant : une
fonction verrouillee peut en appeler une autre qui l'est aussi.
Portee : ce processus. Le lanceur en ligne de commande, lance a part, n'y
est pas soumis -- ne pas le faire tourner en meme temps que la page.
"""
import io
import json
import os
import tempfile
import threading


class EtatIllisible(RuntimeError):
    """Fichier d'etat present mais inexploitable. Jamais avale : le prendre
    pour vide reviendrait a effacer son contenu a la prochaine ecriture."""


def ecrire_texte(chemin, texte):
    dossier = os.path.dirname(os.path.abspath(chemin))
    os.makedirs(dossier, exist_ok=True)
    fd, provisoire = tempfile.mkstemp(
        dir=dossier, prefix="." + os.path.basename(chemin) + ".", suffix=".tmp")
    try:
        with io.open(fd, "w", encoding="utf-8") as f:
            f.write(texte)
            f.flush()
            os.fsync(f.fileno())
        os.replace(provisoire, chemin)
    except BaseException:
        try:
            os.unlink(provisoire)
        except OSError:
            pass
        raise


def ecrire_json(chemin, donnees, **options):
    options.setdefault("ensure_ascii", False)
    options.setdefault("indent", 2)
    ecrire_texte(chemin, json.dumps(donnees, **options))


def lire_json(chemin, attendu, defaut):
    """Contenu du fichier, `defaut` s'il n'existe pas, EtatIllisible s'il
    existe mais ne se lit pas ou n'a pas le type attendu."""
    if not os.path.isfile(chemin):
        return defaut
    try:
        with io.open(chemin, encoding="utf-8") as f:
            donnees = json.load(f)
    except Exception as e:
        raise EtatIllisible("%s illisible : %s" % (chemin, e))
    if not isinstance(donnees, attendu):
        raise EtatIllisible("%s : %s attendu, %s trouvé"
                            % (chemin, attendu.__name__, type(donnees).__name__))
    return donnees


_verrous = {}
_verrou_des_verrous = threading.Lock()


def verrou_societe(dossier_abs):
    cle = os.path.realpath(dossier_abs)
    with _verrou_des_verrous:
        if cle not in _verrous:
            _verrous[cle] = threading.RLock()
        return _verrous[cle]
