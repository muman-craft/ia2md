"""
Perplexity — export conversations-AAAAMMJJ_hhmmss-xxxx.json.

Schéma : {"conversations": [...]}, chaque conversation a ses échanges (entries) dans l'ordre ;
un échange = question (query) + réponse en Markdown (answer) + date. Pas de branches.
Les renvois de citation [1][2]… sont supprimés (l'export ne fournit pas les sources),
sauf dans le code (blocs ``` et code en ligne), laissé intact.
"""

import re

from ia2md_commun import Conversation, Source, Tour, parse_iso

CODE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.S)
RENVOIS = re.compile(r"(?:\[\d+\])+")


def sans_renvois(texte: str) -> str:
    morceaux = CODE.split(texte)  # indices pairs : prose ; impairs : code
    for i in range(0, len(morceaux), 2):
        morceaux[i] = RENVOIS.sub("", morceaux[i])
    return "".join(morceaux).strip()


def lire(source: Source, utilisateur: str, libelle: str):
    for donnees in source.documents():
        for c in donnees.get("conversations") or []:
            tours = []
            for e in c.get("entries") or []:
                date = parse_iso(e.get("created_at"))
                question = (e.get("query") or "").strip()
                reponse = sans_renvois(e.get("answer") or "")
                if question:
                    tours.append(Tour(utilisateur, date, question))
                if reponse:
                    tours.append(Tour(libelle, date, reponse))
            yield Conversation(
                titre=c.get("context_title") or "",
                ident=c.get("context_uuid", ""),
                creee=parse_iso(c.get("created_at")),
                maj=parse_iso(c.get("updated_at")),
                tours=tours,
            )


def valide(document) -> bool:
    """Dictionnaire conversations dont les éléments ont context_uuid et entries."""
    convs = document.get("conversations") if isinstance(document, dict) else None
    return (isinstance(convs, list) and bool(convs) and isinstance(convs[0], dict)
            and "context_uuid" in convs[0] and "entries" in convs[0])
