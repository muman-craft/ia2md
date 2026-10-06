"""
ia2md_commun.py — Partie commune de la conversion des exports d'IA en fichiers .md.

Chaque script d'IA lit son propre schéma et fournit des Conversation ;
ce module se charge de la conversion HTML, du rendu Markdown et de l'écriture sur disque.

Dépendance : html2text (pip install html2text), uniquement pour les exports en HTML.
"""

import ctypes
import json
import os
import re
import shutil
import unicodedata
import zipfile
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


# ---------- modèle commun ----------

@dataclass
class Tour:
    locuteur: str
    date: datetime | None
    texte: str


@dataclass
class Conversation:
    titre: str
    ident: str
    creee: datetime | None
    maj: datetime | None
    tours: list[Tour] = field(default_factory=list)


def parse_iso(s: str | None) -> datetime | None:
    """Date ISO 8601 (avec ou sans 'Z') -> datetime, ou None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------- accès à la source ----------

def nom_zip(info: zipfile.ZipInfo) -> str:
    """Nom d'un membre de zip, décodé correctement et normalisé (NFC)."""
    nom = info.filename
    if not info.flag_bits & 0x800:  # nom non marqué UTF-8 : Python l'a lu en cp437
        try:
            nom = nom.encode("cp437").decode("utf-8")
        except UnicodeError:
            pass
    return unicodedata.normalize("NFC", nom)


@dataclass
class Source:
    """Fichier(s) source d'une IA dans un téléchargement : zip, dossier ou fichier seul.

    conteneur : le téléchargement.
    membres   : chemins des fichiers dans le conteneur (séparateur '/'), triés par nom ;
                [None] si le téléchargement est le fichier lui-même.
    Plusieurs membres quand le motif déclaré est un ensemble (ex. conversations-000.json, -001…).
    """
    conteneur: Path
    membres: list[str | None] = field(default_factory=lambda: [None])

    def _lire(self, membre: str | None) -> str:
        if membre is None:
            return self.conteneur.read_text(encoding="utf-8")
        if self.conteneur.is_dir():
            return (self.conteneur / membre).read_text(encoding="utf-8")
        with zipfile.ZipFile(self.conteneur) as z:
            for info in z.infolist():
                if nom_zip(info) == unicodedata.normalize("NFC", membre):
                    return z.read(info).decode("utf-8")
        raise FileNotFoundError(f"{membre} absent de {self.conteneur.name}")

    def documents(self):
        """Contenu JSON de chaque fichier source, dans l'ordre."""
        for membre in self.membres:
            yield json.loads(self._lire(membre))

    def __str__(self) -> str:
        if self.membres == [None]:
            return self.conteneur.name
        suite = f" (+{len(self.membres) - 1})" if len(self.membres) > 1 else ""
        return f"{self.conteneur.name} > {self.membres[0]}{suite}"


# ---------- conversion HTML ----------

def html_vers_texte(html: str) -> str:
    """HTML -> texte Markdown (titres, listes, tableaux, code), images ignorées."""
    import html2text  # importé ici : seuls les exports HTML en ont besoin
    h = html2text.HTML2Text()
    h.body_width = 0        # pas de retour à la ligne forcé
    h.unicode_snob = True   # garder les accents et caractères typographiques
    h.ignore_images = True
    return h.handle(html or "").strip()


# ---------- rendu ----------

TITRE_DEFAUT = "Sans titre"


def _fmt(d: datetime | None) -> str:
    return d.astimezone().strftime("%Y-%m-%d %H:%M") if d else "?"


def rendre(conv: Conversation) -> str:
    lignes = [
        f"# {conv.titre or TITRE_DEFAUT}",
        "",
        f"Créée : {_fmt(conv.creee)}",
        f"Mise à jour : {_fmt(conv.maj)}",
    ]
    if conv.ident:
        lignes.append(f"Identifiant : {conv.ident}")
    lignes.append("")
    for tour in conv.tours:
        entete = f"## {tour.locuteur} — {_fmt(tour.date)}" if tour.date else f"## {tour.locuteur}"
        lignes += [entete, "", tour.texte, ""]
    return "\n".join(lignes)


# ---------- écriture ----------

LONGUEUR_MAX = 120  # marge sous la limite Windows de 260 caractères du chemin complet
INTERDITS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVES = {"CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


def nettoyer_nom(titre: str) -> str:
    """Ne retire que ce que Windows interdit dans un nom de fichier."""
    nom = re.sub(r"\s+", " ", titre or "").strip()  # retours à la ligne, tabulations -> espace
    nom = INTERDITS.sub("_", nom)
    nom = nom[:LONGUEUR_MAX].rstrip(" .")
    if not nom:
        nom = TITRE_DEFAUT
    if nom.split(".")[0].upper() in RESERVES:
        nom = "_" + nom
    return nom


def chemin_libre(dossier: Path, nom: str) -> Path:
    """Si le fichier existe déjà : 'nom (2)', 'nom (3)'…"""
    chemin = dossier / f"{nom}.md"
    n = 2
    while chemin.exists():
        chemin = dossier / f"{nom} ({n}).md"
        n += 1
    return chemin


def regler_dates(chemin: Path, creee: datetime | None, maj: datetime | None) -> None:
    """Modification = maj ; création = creee (Windows, via SetFileTime)."""
    maj = maj or creee
    if not maj:
        return
    os.utime(chemin, (maj.timestamp(), maj.timestamp()))
    if os.name != "nt" or not creee:
        return

    def filetime(d: datetime) -> wintypes.FILETIME:
        v = int(d.timestamp() * 10_000_000) + 116_444_736_000_000_000
        return wintypes.FILETIME(v & 0xFFFFFFFF, v >> 32)

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    # 0x100 = FILE_WRITE_ATTRIBUTES, 0x7 = partage total, 3 = OPEN_EXISTING, 0x80 = FILE_ATTRIBUTE_NORMAL
    h = k32.CreateFileW(str(chemin), 0x100, 0x7, None, 3, 0x80, None)
    if h == wintypes.HANDLE(-1).value:
        return
    try:
        c, m = filetime(creee), filetime(maj)
        k32.SetFileTime(h, ctypes.byref(c), ctypes.byref(m), ctypes.byref(m))
    finally:
        k32.CloseHandle(h)


def vider(dossier: Path) -> None:
    for f in dossier.glob("*.md"):
        f.unlink()


# ---------- orchestration ----------

TEMP = "_ia2md_temp"  # dossier de génération, dans le dossier de chaque IA


def exporter(conversations: Iterable[Conversation], dossier_sortie: str | Path) -> tuple[int, list[dict]]:
    """Génération transactionnelle ; renvoie (fichiers écrits, titres en double).

    Les .md sont écrits dans <dossier>/_ia2md_temp. En cas de succès seulement, les anciens
    .md sont supprimés et les nouveaux renommés vers le dossier (même disque : renommage
    instantané, dates conservées), puis le temporaire est supprimé. En cas d'erreur,
    l'exception remonte, les anciens .md sont intacts et le temporaire reste en place.

    Titres en double : conversations dont le nom de fichier (après nettoyage, sans tenir
    compte de la casse) est déjà pris ; [{"nom", "conversations": [{"titre", "creee"}]}].
    """
    dossier = Path(dossier_sortie)
    temp = dossier / TEMP
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)

    ecrits = 0
    par_nom: dict[str, list] = {}
    for conv in conversations:
        if not conv.tours:
            continue
        nom = nettoyer_nom(conv.titre)
        par_nom.setdefault(nom.lower(), []).append((nom, conv))
        chemin = chemin_libre(temp, nom)
        chemin.write_text(rendre(conv), encoding="utf-8")
        regler_dates(chemin, conv.creee, conv.maj)
        ecrits += 1

    vider(dossier)
    for f in temp.glob("*.md"):
        os.replace(f, dossier / f.name)
    shutil.rmtree(temp)

    doublons = []
    for liste in par_nom.values():
        if len(liste) > 1:
            convs = sorted((c for _, c in liste), key=lambda c: c.creee.timestamp() if c.creee else 0)
            doublons.append({"nom": liste[0][0], "conversations": [
                {"titre": c.titre or TITRE_DEFAUT,
                 "creee": c.creee.astimezone().strftime("%Y-%m-%d") if c.creee else "?"} for c in convs]})
    doublons.sort(key=lambda d: d["nom"].lower())
    return ecrits, doublons
