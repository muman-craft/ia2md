"""
Gemini — Google Takeout « Applications Gemini » (MonActivité.json).

Une entrée = un tour (prompt dans le titre, réponse en HTML), entrées de la plus récente
à la plus ancienne, conversations entrecoupées. Regroupement par l'identifiant de
l'adresse gemini.google.com/app/<id>, puis remise en ordre chronologique.
Exclut : entrées hors prompt (Canvas, brouillons), images et fichiers joints.
"""

import re

from ia2md_commun import Conversation, Source, Tour, html_vers_texte, parse_iso

# Titre fabriqué à partir du premier prompt (l'export ne contient pas le titre de l'interface) :
# un renommage dans l'interface ne changerait rien, les doublons ne sont donc pas une anomalie.
TITRE_MODIFIABLE = False

PREFIXE_PROMPT = re.compile(r"^Prompt\s*:\s*")
ADRESSE_CONV = "gemini.google.com/app/"


def lire(source: Source, utilisateur: str, libelle: str):
    # identifiant -> liste de (date, prompt, réponse) ; l'ordre d'apparition est conservé
    echanges: dict[str, list] = {}
    for donnees in source.documents():
        for e in donnees:
            titre = e.get("title", "")
            details = e.get("details") or []
            url = details[0].get("url", "") if details else ""
            if not PREFIXE_PROMPT.match(titre) or ADRESSE_CONV not in url:
                continue
            ident = url.rsplit("/", 1)[-1]
            prompt = PREFIXE_PROMPT.sub("", titre).strip()
            html = "".join(item.get("html", "") for item in e.get("safeHtmlItem") or [])
            echanges.setdefault(ident, []).append((parse_iso(e.get("time")), prompt, html_vers_texte(html)))

    for ident, liste in echanges.items():
        liste.sort(key=lambda x: x[0].timestamp() if x[0] else 0)
        tours = []
        for date, prompt, reponse in liste:
            if prompt:
                tours.append(Tour(utilisateur, date, prompt))
            if reponse:
                tours.append(Tour(libelle, date, reponse))
        yield Conversation(
            titre=liste[0][1],
            ident=ident,
            creee=liste[0][0],
            maj=liste[-1][0],
            tours=tours,
        )


ENTETE = "Applications Gemini"


def valide(document) -> bool:
    """Liste d'activités Google dont l'en-tête est celui de Gemini (espaces normalisées)."""
    return isinstance(document, list) and any(
        isinstance(e, dict) and " ".join(str(e.get("header", "")).split()) == ENTETE
        for e in document[:50])
