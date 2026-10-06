"""
GoogleAI — Google Takeout « Mode IA » (MonActivité.json).

Une entrée = une conversation, tout le dialogue dans un seul bloc HTML, tours annoncés
par des marqueurs en gras. Le découpage se fait sur ces marqueurs.
Une seule date par conversation ; pas d'identifiant ; pas de date par tour.
"""

import re

from ia2md_commun import Conversation, Source, Tour, html_vers_texte, parse_iso

# Titre fabriqué à partir du premier prompt (l'export ne contient pas le titre de l'interface) :
# un renommage dans l'interface ne changerait rien, les doublons ne sont donc pas une anomalie.
TITRE_MODIFIABLE = False

# Marqueurs de tour tels qu'écrits par Google (compte en français, espace insécable avant ':')
MARQUEURS = {
    "Votre prompt": "utilisateur",
    "Réponse de la Recherche": "ia",
}
PREFIXE_TITRE = re.compile(r"^Vous avez recherché\s*")
DECOUPE = re.compile(r"<strong>(" + "|".join(map(re.escape, MARQUEURS)) + r")\s*:</strong>")


def decouper(html: str, noms: dict) -> list[Tour]:
    morceaux = DECOUPE.split(html)  # [avant, marqueur, segment, marqueur, segment, ...]
    tours = []
    for marqueur, segment in zip(morceaux[1::2], morceaux[2::2]):
        texte = html_vers_texte(segment)
        if texte:
            tours.append(Tour(noms[MARQUEURS[marqueur]], None, texte))
    return tours


def lire(source: Source, utilisateur: str, libelle: str):
    noms = {"utilisateur": utilisateur, "ia": libelle}
    for donnees in source.documents():
        for e in donnees:
            html = "".join(item.get("html", "") for item in e.get("safeHtmlItem") or [])
            if not html:
                continue
            titre = PREFIXE_TITRE.sub("", e.get("title", "")).strip()
            tours = decouper(html, noms)
            if not tours:
                print(f"    Aucun marqueur trouvé, conversation non découpée : {titre[:80]}")
                tours = [Tour("Non découpé", None, html_vers_texte(html))]
            date = parse_iso(e.get("time"))
            yield Conversation(titre=titre, ident="", creee=date, maj=date, tours=tours)


ENTETE = "Mode IA"


def valide(document) -> bool:
    """Liste d'activités Google dont l'en-tête est celui du Mode IA (espaces normalisées)."""
    return isinstance(document, list) and any(
        isinstance(e, dict) and " ".join(str(e.get("header", "")).split()) == ENTETE
        for e in document[:50])
