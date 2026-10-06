"""
Claude — export conversations.json.

Garde : texte de la discussion + textes collés (pièces jointes à contenu extrait).
Exclut : raisonnement, appels d'outils et leurs résultats, images, fichiers.
"""

from ia2md_commun import Conversation, Source, Tour, parse_iso


def texte_message(msg: dict) -> str:
    blocs = msg.get("content") or []
    if blocs:
        parties = [b.get("text", "") for b in blocs if b.get("type") == "text"]
    else:
        parties = [msg.get("text", "")]
    texte = "\n\n".join(p.strip() for p in parties if p and p.strip())

    for pj in msg.get("attachments") or []:
        colle = (pj.get("extracted_content") or "").strip()
        if colle:
            nom = pj.get("file_name") or "texte collé"
            texte += f"\n\n--- {nom} ---\n\n{colle}\n\n--- fin {nom} ---"
    return texte.strip()


def lire(source: Source, utilisateur: str, libelle: str):
    locuteurs = {"human": utilisateur, "assistant": libelle}
    for donnees in source.documents():
        for c in donnees:
            tours = []
            for msg in c.get("chat_messages") or []:
                texte = texte_message(msg)
                if texte:
                    qui = locuteurs.get(msg.get("sender"), msg.get("sender", "?"))
                    tours.append(Tour(qui, parse_iso(msg.get("created_at")), texte))
            yield Conversation(
                titre=c.get("name") or "",
                ident=c.get("uuid", ""),
                creee=parse_iso(c.get("created_at")),
                maj=parse_iso(c.get("updated_at")),
                tours=tours,
            )


def valide(document) -> bool:
    """Liste de conversations portant chat_messages."""
    return (isinstance(document, list) and bool(document) and isinstance(document[0], dict)
            and "chat_messages" in document[0])
