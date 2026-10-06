"""
Grok — export prod-grok-backend.json.

Schéma : {"conversations": [{"conversation": {...}, "responses": [{"response": {...}}]}]}
Messages en arbre (parent_response_id), dates des messages au format MongoDB (ms depuis 1970).
Branches (réponses régénérées, comparaisons côte à côte) : on garde la branche qui contient
le message le plus récent ; leaf_response_id est inutilisable (il désigne le plus souvent
un message absent de l'export).
Exclut : raisonnement, étapes, recherches web, publications X, pièces jointes, images générées.
"""

import re
from datetime import datetime, timezone

from ia2md_commun import Conversation, Source, Tour, parse_iso

CARTES = re.compile(r"<grok:render\b.*?</grok:render>", re.S)  # cartes de citation inline


def date_mongo(v) -> datetime | None:
    """{"$date": {"$numberLong": "ms"}} -> datetime ; accepte aussi une chaîne ISO."""
    if isinstance(v, dict):
        d = v.get("$date")
        if isinstance(d, dict) and "$numberLong" in d:
            return datetime.fromtimestamp(int(d["$numberLong"]) / 1000, tz=timezone.utc)
        if isinstance(d, str):
            return parse_iso(d)
    if isinstance(v, str):
        return parse_iso(v)
    return None


def _cle_temps(r: dict) -> float:
    d = date_mongo(r.get("create_time"))
    return d.timestamp() if d else 0


def chemin_retenu(reponses: list[dict]) -> list[dict]:
    """Branche qui contient le message le plus récent, de la racine à ce message."""
    if not reponses:
        return []
    par_id = {r["_id"]: r for r in reponses}
    noeud = max(reponses, key=_cle_temps)
    chemin = []
    while noeud is not None and noeud not in chemin:
        chemin.append(noeud)
        noeud = par_id.get(noeud.get("parent_response_id"))
    return chemin[::-1]


def lire(source: Source, utilisateur: str, libelle: str):
    # sender 'human' -> utilisateur ; toute autre valeur (assistant, ASSISTANT, grok-4-auto…) -> IA
    for donnees in source.documents():
        for bloc in donnees.get("conversations") or []:
            conv = bloc.get("conversation") or {}
            reponses = [x["response"] for x in bloc.get("responses") or [] if x.get("response")]
            tours = []
            for r in chemin_retenu(reponses):
                texte = CARTES.sub("", r.get("message") or "").strip()
                if texte:
                    qui = utilisateur if r.get("sender") == "human" else libelle
                    tours.append(Tour(qui, date_mongo(r.get("create_time")), texte))
            yield Conversation(
                titre=conv.get("title") or "",
                ident=conv.get("id", ""),
                creee=parse_iso(conv.get("create_time")),
                maj=parse_iso(conv.get("modify_time")),
                tours=tours,
            )


def valide(document) -> bool:
    """Dictionnaire conversations dont les éléments ont conversation et responses."""
    convs = document.get("conversations") if isinstance(document, dict) else None
    return (isinstance(convs, list) and bool(convs) and isinstance(convs[0], dict)
            and "conversation" in convs[0] and "responses" in convs[0])
