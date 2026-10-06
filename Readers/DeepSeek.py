"""
DeepSeek — export conversations.json.

Messages en arbre (mapping : parent / enfants), texte déjà en Markdown.
Locuteur déduit du type de fragment : REQUEST = utilisateur, RESPONSE = DeepSeek.
Branches (prompt modifié, réponse régénérée) : on suit le dernier enfant, le plus récent.
Exclut : raisonnement (THINK), recherches et outils, fichiers joints (seul leur nom est exporté).
"""

from ia2md_commun import Conversation, Source, Tour, parse_iso


def chemin_retenu(mapping: dict) -> list[dict]:
    """Nœuds de la racine à la feuille, en prenant le dernier enfant à chaque bifurcation."""
    noeud = next((n for n in mapping.values() if n.get("parent") is None), None)
    chemin = []
    while noeud and noeud.get("children"):
        noeud = mapping[noeud["children"][-1]]
        chemin.append(noeud)
    return chemin


def tours_du_message(msg: dict, locuteurs: dict) -> list[Tour]:
    date = parse_iso(msg.get("inserted_at"))
    tours = []
    for frag in msg.get("fragments") or []:
        qui = locuteurs.get(frag.get("type"))
        texte = (frag.get("content") or "").strip()
        if not qui or not texte:
            continue
        if tours and tours[-1].locuteur == qui:  # fragments consécutifs du même locuteur
            tours[-1].texte += "\n\n" + texte
        else:
            tours.append(Tour(qui, date, texte))
    return tours


def lire(source: Source, utilisateur: str, libelle: str):
    locuteurs = {"REQUEST": utilisateur, "RESPONSE": libelle}  # seuls types de fragment conservés
    for donnees in source.documents():
        for c in donnees:
            tours = []
            for noeud in chemin_retenu(c.get("mapping") or {}):
                if noeud.get("message"):
                    tours += tours_du_message(noeud["message"], locuteurs)
            yield Conversation(
                titre=c.get("title") or "",
                ident=c.get("id", ""),
                creee=parse_iso(c.get("inserted_at")),
                maj=parse_iso(c.get("updated_at")),
                tours=tours,
            )


def valide(document) -> bool:
    """Liste de conversations en arbre dont les messages sont faits de fragments."""
    if not (isinstance(document, list) and document and isinstance(document[0], dict)):
        return False
    mapping = document[0].get("mapping")
    return isinstance(mapping, dict) and any(
        isinstance(n, dict) and isinstance(n.get("message"), dict) and "fragments" in n["message"]
        for n in mapping.values())
