"""
ChatGPT — export découpé en conversations-NNN.json (motif déclaré dans ia2md.toml).

Messages en arbre (mapping, chaque nœud connaît son parent) ; current_node désigne la
branche retenue : on remonte de lui jusqu'à la racine. Dates en secondes depuis 1970.
Garde : texte des rôles user / assistant, transcriptions des échanges vocaux.
Exclut : raisonnement, messages masqués, images et audio, messages vides.
"""

import json
import re
from datetime import datetime, timezone

from ia2md_commun import Conversation, Source, Tour

TYPES_GARDES = {"text", "multimodal_text"}

# Marqueurs à caractères privés : \ue200type\ue202[arguments]\ue201
MARQUEUR = re.compile(r"\ue200(\w+)\ue202(.*?)\ue201", re.S)


def remplacer_marqueur(m: re.Match) -> str:
    """entity -> le nom de l'entité ; tout autre marqueur (citations…) -> supprimé."""
    if m.group(1) == "entity":
        try:
            args = json.loads(m.group(2))
            return str(args[1] if len(args) > 1 else args[0])
        except (ValueError, IndexError, TypeError):
            return ""
    return ""


def date_epoch(v) -> datetime | None:
    return datetime.fromtimestamp(v, tz=timezone.utc) if isinstance(v, (int, float)) else None


def chemin_retenu(conv: dict) -> list[dict]:
    """Nœuds de la racine jusqu'à current_node."""
    mapping = conv.get("mapping") or {}
    cle, chemin = conv.get("current_node"), []
    while cle in mapping and len(chemin) <= len(mapping):
        chemin.append(mapping[cle])
        cle = mapping[cle].get("parent")
    return chemin[::-1]


def texte_message(msg: dict) -> str:
    contenu = msg.get("content") or {}
    if contenu.get("content_type") not in TYPES_GARDES:
        return ""
    if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
        return ""
    parties = []
    for p in contenu.get("parts") or []:
        if isinstance(p, str):
            parties.append(p)
        elif isinstance(p, dict) and p.get("content_type") == "audio_transcription":
            parties.append(p.get("text") or "")
    texte = "\n\n".join(x.strip() for x in parties if x and x.strip())
    return MARQUEUR.sub(remplacer_marqueur, texte).strip()


def lire(source: Source, utilisateur: str, libelle: str):
    locuteurs = {"user": utilisateur, "assistant": libelle}
    for donnees in source.documents():
        for c in donnees:
            tours = []
            for noeud in chemin_retenu(c):
                msg = noeud.get("message")
                if not msg:
                    continue
                qui = locuteurs.get((msg.get("author") or {}).get("role"))
                texte = texte_message(msg) if qui else ""
                if texte:
                    tours.append(Tour(qui, date_epoch(msg.get("create_time")), texte))
            yield Conversation(
                titre=c.get("title") or "",
                ident=c.get("id") or c.get("conversation_id", ""),
                creee=date_epoch(c.get("create_time")),
                maj=date_epoch(c.get("update_time")),
                tours=tours,
            )


def valide(document) -> bool:
    """Liste de conversations en arbre avec current_node."""
    return (isinstance(document, list) and bool(document) and isinstance(document[0], dict)
            and "mapping" in document[0] and "current_node" in document[0])
