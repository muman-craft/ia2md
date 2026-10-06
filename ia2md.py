"""
ia2md.py — Superviseur des exports d'IA.

Déroulé d'une séance :
1. Arrivées : examine le dossier Téléchargements de Windows (ou [racines] arrivee) ; chaque
   zip/JSON reconnu par un lecteur (motif source, en-tête, puis valide()) est proposé au
   rangement dans son dossier d'archives.
2. Évaluation : pour chaque IA de ia2md.toml, trouve sa source dans le téléchargement le plus
   récent qui la contient et établit son état.
3. Génération : sur choix, valide l'archive,* puis génère les .md avec Readers/<IA>.py de façon
   transactionnelle (dossier _ia2md_temp), écrit le journal.
4. Rapport ia2md-rapport.md, avec les opérations de la séance.

États :
- Nouveau             : pas de journal ;
- À mettre à jour     : la source a changé depuis la dernière génération ;
- Version changée     : même source, mais version majeure ou mineure différente ;
- À jour              : même source, même version majeure.mineure ;
- Source introuvable / Lecteur introuvable : anomalies, pas de génération possible.
Un dossier _ia2md_temp présent dans le dossier d'une IA signale une génération interrompue.

Règle de la source :
- nom exact       -> un seul fichier attendu ; plusieurs = anomalie ;
- motif à joker   -> ensemble de fichiers (*, ?, [0-9]…), triés par nom ;
- en-tête déclaré -> on ne garde que les fichiers dont les entrées portent cet en-tête.

Journal : ia2md.json dans le dossier de sortie de chaque IA, écrit après une génération
réussie. Identité de la source = taille et CRC32 de chaque fichier (le nom ne compte pas).

Nécessite Python 3.11 ou plus (tomllib).
"""

VERSION = "1.0.0"  # majeure.mineure.corrective — majeure ou mineure changée = régénération

import ctypes
import fnmatch
import importlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
import unicodedata
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))

from ia2md_commun import TEMP, Source, exporter, nom_zip

DEFINITIONS = ICI / "ia2md.toml"
RAPPORT = ICI / "ia2md-rapport.md"
JOURNAL = "ia2md.json"   # dans le dossier de sortie de chaque IA
PAQUET_LECTEURS = "Readers"

# TEST : True = écrit le journal de chaque IA dont la source est trouvée, SANS génération
# (simule une génération réussie), à la place du dialogue. Remettre à False après les tests.
FORCER_JOURNAL = False

NOUVEAU = "Nouveau"
A_METTRE_A_JOUR = "À mettre à jour"
VERSION_CHANGEE = "Version changée"
A_JOUR = "À jour"
SOURCE_INTROUVABLE = "Source introuvable"
LECTEUR_INTROUVABLE = "Lecteur introuvable"
A_TRAITER = {NOUVEAU, A_METTRE_A_JOUR, VERSION_CHANGEE}


# ---------- définitions ----------

@dataclass
class IA:
    nom: str
    download: Path       # dossier des téléchargements
    sortie: Path         # dossier md-files
    source: str          # nom exact ou motif à joker
    entete: str | None   # discriminant de contenu (téléchargement partagé)
    libelle: str         # libellé de l'IA dans les tours
    lecteur: str         # module dans Readers

    @property
    def ensemble(self) -> bool:
        return any(c in self.source for c in "*?[")


def dossier_telechargements() -> Path:
    """Dossier Téléchargements de l'utilisateur, demandé à Windows (suit un déplacement)."""
    if os.name == "nt":
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")  # FOLDERID_Downloads
        guid = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        p = ctypes.c_void_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p)) == 0:
            try:
                return Path(ctypes.wstring_at(p))
            finally:
                ctypes.windll.ole32.CoTaskMemFree(p)
    return Path.home() / "Downloads"


def charger_definitions(chemin: Path = DEFINITIONS) -> tuple[dict, list[IA]]:
    with open(chemin, "rb") as f:
        d = tomllib.load(f)
    racines = {k: Path(v) for k, v in d["racines"].items()}
    general = d.get("general", {})
    ias = []
    for nom, v in d.get("ia", {}).items():
        ias.append(IA(
            nom=nom,
            download=racines["downloads"] / v.get("download", nom),
            sortie=racines["exports"] / nom / general.get("sortie", "md-files"),
            source=v["source"],
            entete=v.get("entete"),
            libelle=v.get("libelle", nom),
            lecteur=v.get("lecteur", nom),
        ))
    brut = d["racines"].get("arrivee", "")
    racines.pop("arrivee", None)
    config = {"racines": racines,
              "arrivee": Path(brut) if brut else dossier_telechargements(),
              "utilisateur": general.get("utilisateur", "Utilisateur")}
    return config, ias


# ---------- recherche de la source ----------

def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _espaces(s: str) -> str:
    """Espaces normalisées (insécables comprises) pour comparer des en-têtes."""
    return " ".join((s or "").split())


def _correspond(nom: str, motif: str) -> bool:
    base = _nfc(nom.replace("\\", "/").rsplit("/", 1)[-1]).lower()
    return fnmatch.fnmatchcase(base, _nfc(motif).lower())


def telechargements(dossier: Path) -> list[Path]:
    """Zip, fichiers et dossiers du dossier de téléchargement, du plus récent au plus ancien."""
    if not dossier.is_dir():
        return []
    return sorted(dossier.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)


def membres_correspondants(telechargement: Path, motif: str) -> list[str | None]:
    if telechargement.is_dir():
        return sorted(p.relative_to(telechargement).as_posix()
                      for p in telechargement.rglob("*") if p.is_file() and _correspond(p.name, motif))
    if zipfile.is_zipfile(telechargement):
        with zipfile.ZipFile(telechargement) as z:
            return sorted(nom_zip(i) for i in z.infolist() if not i.is_dir() and _correspond(nom_zip(i), motif))
    return [None] if _correspond(telechargement.name, motif) else []


def a_entete(telechargement: Path, membre: str | None, entete: str) -> bool:
    """Vrai si les entrées du fichier (export d'activité Google) portent cet en-tête."""
    try:
        donnees = next(Source(telechargement, [membre]).documents())
    except (ValueError, OSError, StopIteration):
        return False
    cible = _espaces(entete)
    return isinstance(donnees, list) and any(
        isinstance(e, dict) and _espaces(e.get("header", "")) == cible for e in donnees[:50])


def trouver_source(ia: IA) -> tuple[Source | None, str, int]:
    """(source, anomalie, nombre de téléchargements)."""
    if not ia.download.is_dir():
        return None, f"dossier absent : {ia.download.name}", 0
    liste = telechargements(ia.download)
    for t in liste:
        membres = membres_correspondants(t, ia.source)
        if ia.entete:
            membres = [m for m in membres if a_entete(t, m, ia.entete)]
        if not membres:
            continue
        if len(membres) > 1 and not ia.ensemble:
            return None, f"{len(membres)} fichiers {ia.source} dans {t.name}, un seul attendu", len(liste)
        return Source(t, membres), "", len(liste)
    if not liste:
        return None, "aucun téléchargement", 0
    return None, f"aucun {ia.source} dans les {len(liste)} téléchargement(s)", len(liste)


# ---------- identité de la source et journal ----------

def _crc_fichier(chemin: Path) -> int:
    crc = 0
    with open(chemin, "rb") as f:
        while bloc := f.read(1 << 20):
            crc = zlib.crc32(bloc, crc)
    return crc


def identite(source: Source) -> list[dict]:
    """Nom, taille et CRC32 de chaque fichier source (lus dans le zip sans décompresser)."""
    c = source.conteneur
    if source.membres == [None]:
        return [{"nom": c.name, "taille": c.stat().st_size, "crc": _crc_fichier(c)}]
    if c.is_dir():
        return [{"nom": m, "taille": (c / m).stat().st_size, "crc": _crc_fichier(c / m)}
                for m in source.membres]
    with zipfile.ZipFile(c) as z:
        index = {nom_zip(i): i for i in z.infolist()}
    return [{"nom": m, "taille": index[m].file_size, "crc": index[m].CRC} for m in source.membres]


def _empreinte(fichiers: list[dict]) -> list[tuple]:
    """Contenu seul (taille, CRC32) : un nom ou un chemin interne qui change d'un export
    à l'autre (horodatage Perplexity, dossier aléatoire Grok) ne compte pas."""
    return [(f.get("taille"), f.get("crc")) for f in fichiers]


def _majeure_mineure(v: str | None) -> tuple | None:
    try:
        return tuple(int(x) for x in (v or "").split(".")[:2])
    except ValueError:
        return None


def lire_journal(ia: IA) -> dict | None:
    try:
        with open(ia.sortie / JOURNAL, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def ecrire_journal(ia: IA, source: Source, nb_md: int, doublons: list | None = None) -> None:
    """À appeler uniquement après une génération réussie."""
    st = source.conteneur.stat()
    journal = {
        "version": VERSION,
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "telechargement": {"nom": source.conteneur.name,
                           "date": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")},
        "source": identite(source),
        "fichiers_md": nb_md,
        "doublons": doublons or [],
    }
    ia.sortie.mkdir(parents=True, exist_ok=True)
    with open(ia.sortie / JOURNAL, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=2)


# ---------- lecteurs ----------

def charger_lecteur(ia: IA) -> tuple[ModuleType | None, str]:
    try:
        module = importlib.import_module(f"{PAQUET_LECTEURS}.{ia.lecteur}")
    except ImportError as e:
        return None, f"{PAQUET_LECTEURS}/{ia.lecteur}.py : {e}"
    if not callable(getattr(module, "lire", None)):
        return None, f"{PAQUET_LECTEURS}/{ia.lecteur}.py n'a pas de fonction lire()"
    return module, ""


# ---------- évaluation ----------

@dataclass
class Evaluation:
    ia: IA
    etat: str
    detail: str
    source: Source | None
    module: ModuleType | None
    nb_telechargements: int
    nb_md: int | None
    journal: dict | None
    interrompu: bool = False

    @property
    def generable(self) -> bool:
        return self.source is not None and self.module is not None

    @property
    def doublons(self) -> list[dict]:
        return (self.journal or {}).get("doublons", [])

    @property
    def doublons_anomalie(self) -> bool:
        """Doublons à corriger : seulement si le titre exporté est modifiable dans l'interface."""
        return bool(self.doublons) and getattr(self.module, "TITRE_MODIFIABLE", True)


def nb_md(dossier: Path) -> int | None:
    return sum(1 for _ in dossier.glob("*.md")) if dossier.is_dir() else None


def evaluer(ia: IA) -> Evaluation:
    source, anomalie, nb = trouver_source(ia)
    module, erreur = charger_lecteur(ia)
    journal = lire_journal(ia)
    ev = Evaluation(ia, "", "", source, module, nb, nb_md(ia.sortie), journal,
                    (ia.sortie / TEMP).is_dir())
    if not source:
        ev.etat, ev.detail = SOURCE_INTROUVABLE, anomalie
    elif not module:
        ev.etat, ev.detail = LECTEUR_INTROUVABLE, erreur
    elif journal is None:
        ev.etat, ev.detail = NOUVEAU, "jamais généré"
    elif _empreinte(journal.get("source", [])) != _empreinte(identite(source)):
        ev.etat, ev.detail = A_METTRE_A_JOUR, "source modifiée"
    elif _majeure_mineure(journal.get("version")) != _majeure_mineure(VERSION):
        ev.etat, ev.detail = VERSION_CHANGEE, f"version {journal.get('version', '?')} → {VERSION}"
    else:
        ev.etat, ev.detail = A_JOUR, ""
    return ev


# ---------- arrivées ----------

SEANCE: list[str] = []  # opérations de la séance, pour le rapport


def _premier_document(t: Path, membres: list, cache: dict):
    cle = (t, membres[0])
    if cle not in cache:
        try:
            cache[cle] = next(Source(t, membres[:1]).documents())
        except Exception:
            cache[cle] = None
    return cache[cle]


def identifier(fichier: Path, ias: list[IA]) -> list[IA]:
    """IA dont le lecteur reconnaît ce fichier : motif source, en-tête, puis valide()."""
    trouvees, cache = [], {}
    for ia in ias:
        membres = membres_correspondants(fichier, ia.source)
        if ia.entete:
            membres = [m for m in membres if a_entete(fichier, m, ia.entete)]
        if not membres or (len(membres) > 1 and not ia.ensemble):
            continue
        module, _ = charger_lecteur(ia)
        valide = getattr(module, "valide", None)
        if callable(valide) and valide(_premier_document(fichier, membres, cache)):
            trouvees.append(ia)
    return trouvees


def destination_libre(dossier: Path, nom: str) -> Path:
    """Même règle que pour les .md : 'nom (2).zip', 'nom (3).zip'…"""
    chemin = dossier / nom
    base, ext = Path(nom).stem, Path(nom).suffix
    n = 2
    while chemin.exists():
        chemin = dossier / f"{base} ({n}){ext}"
        n += 1
    return chemin


def ranger_arrivees(arrivee: Path, ias: list[IA]) -> None:
    if not arrivee.is_dir():
        print(f"Dossier d'arrivée absent : {arrivee}")
        return
    propositions, ambigus = [], []
    for f in sorted(arrivee.iterdir(), key=lambda p: p.stat().st_mtime):
        if not (f.is_file() and f.suffix.lower() in (".zip", ".json")):
            continue
        trouvees = identifier(f, ias)
        dossiers = {ia.download for ia in trouvees}
        if len(dossiers) == 1:
            propositions.append((f, dossiers.pop(), [ia.nom for ia in trouvees]))
        elif len(dossiers) > 1:
            ambigus.append((f, [ia.nom for ia in trouvees]))

    for f, noms in ambigus:
        anomalie_seance(f"Arrivée ambiguë, laissée en place : {f.name} ({', '.join(noms)})")
    if not propositions:
        return
    print(f"\nArrivées dans {arrivee} :")
    larg = max(len(f.name) for f, _, _ in propositions)
    for f, dossier, noms in propositions:
        st = f.stat()
        cible = dossier.name + (f" ({', '.join(noms)})" if dossier.name not in noms or len(noms) > 1 else "")
        print(f"  {f.name:<{larg}}  {_date(st.st_mtime)[:10]}  {_taille(st.st_size):>8}  → {cible}")
    if input("Ranger ? [Entrée] = oui, 0 = non : ").strip() == "0":
        SEANCE.append(f"{len(propositions)} arrivée(s) non rangée(s) sur décision")
        return
    for f, dossier, _ in propositions:
        dossier.mkdir(parents=True, exist_ok=True)
        cible = destination_libre(dossier, f.name)
        try:
            shutil.move(str(f), str(cible))
        except OSError as ex:
            anomalie_seance(f"Rangement impossible : {f.name} ({ex})")
            continue
        SEANCE.append(f"Rangé : {f.name} → `{cible}`")


# ---------- console ----------

COULEURS = sys.stdout.isatty()
if COULEURS and os.name == "nt":
    os.system("")  # active les séquences de couleur dans l'ancienne console Windows

JAUNE, ROUGE, VERT = "33", "31", "32"
ANOMALIES_SEANCE: list[str] = []  # refus, erreurs, ambiguïtés : ouvrent le rapport


def couleur(texte: str, code: str) -> str:
    return f"\033[{code}m{texte}\033[0m" if COULEURS else texte


def couleur_etat(etat: str) -> str:
    if etat in A_TRAITER:
        return JAUNE
    if etat in (SOURCE_INTROUVABLE, LECTEUR_INTROUVABLE):
        return ROUGE
    return VERT


def console_dediee() -> bool:
    """Vrai si la console a été ouverte pour ce seul processus (double-clic) ;
    faux dans un terminal, VSCode ou sans console."""
    if os.name != "nt":
        return False
    liste = (ctypes.c_uint32 * 4)()
    return ctypes.windll.kernel32.GetConsoleProcessList(liste, 4) == 1


def anomalie_seance(msg: str) -> None:
    print(f"  {couleur(msg, ROUGE)}")
    SEANCE.append(msg)
    ANOMALIES_SEANCE.append(msg)


# ---------- affichage et dialogue ----------

def _date(t: float | datetime) -> str:
    d = t if isinstance(t, datetime) else datetime.fromtimestamp(t)
    return d.strftime("%Y-%m-%d %H:%M")


def afficher(evals: list[Evaluation]) -> None:
    larg = max(len(e.ia.nom) for e in evals)
    print()
    print(f" #  {'IA':<{larg}}  {'État':<19} {'Téléchargement':<36} {'.md':>5}")
    for i, e in enumerate(evals, 1):
        if e.source:
            dl = f"{_date(e.source.conteneur.stat().st_mtime)[:10]}  {e.source.conteneur.name}"
            dl = dl if len(dl) <= 36 else dl[:35] + "…"
        else:
            dl = e.detail[:36]
        md = "—" if e.nb_md is None else str(e.nb_md)
        etat = couleur(f"{e.etat:<19}", couleur_etat(e.etat))
        print(f"{i:>2}  {e.ia.nom:<{larg}}  {etat} {dl:<36} {md:>5}")
        if e.etat in (LECTEUR_INTROUVABLE,):
            print(f"    {couleur(e.detail, ROUGE)}")
        if e.interrompu:
            print(f"    {couleur(f'génération interrompue : {e.ia.sortie / TEMP}', ROUGE)}")


def demander(evals: list[Evaluation]) -> list[Evaluation]:
    a_traiter = [i for i, e in enumerate(evals, 1) if e.etat in A_TRAITER]
    if a_traiter:
        invite = (f"\nGénérer ? [Entrée] = les {len(a_traiter)} à traiter ({' '.join(map(str, a_traiter))}),"
                  f" numéros = choix, * = toutes, 0 = rien : ")
    else:
        invite = "\nTout est à jour. Numéros à régénérer, * = toutes, [Entrée] = rien : "
    reponse = input(invite).replace(",", " ").split()
    if not reponse:
        choix = a_traiter
    elif reponse == ["0"]:
        choix = []
    elif reponse == ["*"]:
        choix = [i for i, e in enumerate(evals, 1) if e.generable]
    else:
        choix = []
        for r in reponse:
            if r.isdigit() and 1 <= int(r) <= len(evals):
                choix.append(int(r))
            else:
                print(f"  ignoré : {r}")
    retenus = []
    for i in dict.fromkeys(choix):  # sans doublon, ordre conservé
        e = evals[i - 1]
        if e.generable:
            retenus.append(e)
        else:
            print(f"  {e.ia.nom} ignoré : {e.etat.lower()}")
    return retenus


def generer(e: Evaluation, utilisateur: str) -> None:
    print(f"\n{e.ia.nom} : génération depuis {e.source}")
    valide = getattr(e.module, "valide", None)
    try:
        ok = callable(valide) and valide(next(e.source.documents()))
    except Exception:
        ok = False
    if not ok:  # rien n'est touché dans le dossier de l'IA
        anomalie_seance(f"{e.ia.nom} : archive non valide, génération refusée ({e.source})")
        return
    try:
        ecrits, doublons = exporter(e.module.lire(e.source, utilisateur, e.ia.libelle), e.ia.sortie)
    except Exception as ex:  # anciens .md intacts ; temporaire conservé pour examen
        anomalie_seance(f"{e.ia.nom} : ERREUR {type(ex).__name__} : {ex} — anciens .md intacts,"
                        f" voir `{e.ia.sortie / TEMP}`")
        return
    ecrire_journal(e.ia, e.source, ecrits, doublons)
    avant = e.journal.get("fichiers_md") if e.journal else None
    if isinstance(avant, int):
        ecart = ecrits - avant
        bilan = f"{avant} → {ecrits} ({ecart:+d})"
        bilan_console = couleur(bilan, ROUGE) if ecart < 0 else bilan
    else:
        bilan = bilan_console = f"{ecrits} fichiers (première génération)"
    titres = f"{len(doublons)} titre(s) en double" if doublons else "aucun titre en double"
    if doublons and getattr(e.module, "TITRE_MODIFIABLE", True):
        titres = couleur(titres, ROUGE)
    print(f"  {bilan_console}, {titres}")
    SEANCE.append(f"{e.ia.nom} : généré depuis `{e.source}`, {bilan}")
    if isinstance(avant, int) and ecrits < avant:  # filet de sécurité : export tronqué ou réinitialisé ?
        ANOMALIES_SEANCE.append(f"{e.ia.nom} : baisse du nombre de conversations ({bilan})")
        SEANCE.append(f"**{e.ia.nom} : baisse du nombre de conversations**")


# ---------- rapport ----------

def _taille(octets: int) -> str:
    for unite, seuil in (("Go", 1 << 30), ("Mo", 1 << 20), ("ko", 1 << 10)):
        if octets >= seuil:
            return f"{octets / seuil:.1f} {unite}".replace(".", ",")
    return f"{octets} o"


def infos_membres(source: Source) -> list[tuple[str, int, str]]:
    """(nom, taille, date) de chaque fichier source."""
    c = source.conteneur
    if source.membres == [None]:
        st = c.stat()
        return [(c.name, st.st_size, _date(st.st_mtime))]
    if c.is_dir():
        return [(m, (c / m).stat().st_size, _date((c / m).stat().st_mtime)) for m in source.membres]
    with zipfile.ZipFile(c) as z:
        index = {nom_zip(i): i for i in z.infolist()}
    return [(m, index[m].file_size, _date(datetime(*index[m].date_time))) for m in source.membres]


def tableau(entetes: list[str], alignements: str, rangees: list[tuple]) -> list[str]:
    """Tableau Markdown aligné en texte brut ; alignements : '<' gauche, '>' droite par colonne."""
    larg = [max(len(str(x)) for x in col) for col in zip(entetes, *rangees)]

    def ligne(valeurs) -> str:
        cases = [str(v).rjust(w) if a == ">" else str(v).ljust(w)
                 for v, w, a in zip(valeurs, larg, alignements)]
        return "| " + " | ".join(cases) + " |"

    traits = ["-" * (w - 1) + ":" if a == ">" else "-" * w for w, a in zip(larg, alignements)]
    return [ligne(entetes), "| " + " | ".join(traits) + " |"] + [ligne(r) for r in rangees]


def dossiers_non_declares(racine: Path, ias: list[IA]) -> list[str]:
    declares = {ia.download.name.lower() for ia in ias}
    if not racine.is_dir():
        return []
    return sorted(p.name for p in racine.iterdir() if p.is_dir() and p.name.lower() not in declares)


def _court(titre: str, n: int = 80) -> str:
    """Titre sur une ligne, tronqué pour le rapport."""
    t = " ".join(str(titre).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def ecrire_rapport(evals: list[Evaluation], config: dict) -> tuple[Path, bool]:
    lignes = [f"# Rapport des exports d'IA — {_date(datetime.now())}", "",
              f"ia2md {VERSION} — définitions : `{DEFINITIONS}` — arrivée : `{config['arrivee']}`", "",
              "## Séance", ""]
    lignes += [f"- {op}" for op in SEANCE] if SEANCE else ["- Aucune opération"]
    lignes.append("")
    anomalies = []
    for e in evals:
        ia = e.ia
        etat = f"**{e.etat}**" if e.etat != A_JOUR else e.etat
        if e.detail:
            etat += f" — {e.detail}"
        lignes += [f"## {ia.nom}", "", f"- État : {etat}"]
        if e.journal:
            t = e.journal.get("telechargement", {})
            lignes.append(f"- Dernière génération : {e.journal.get('genere_le', '?')[:16].replace('T', ' ')}"
                          f" (ia2md {e.journal.get('version', '?')}) depuis `{t.get('nom', '?')}`,"
                          f" {e.journal.get('fichiers_md', '?')} fichiers .md")
        lignes.append(f"- Téléchargements : `{ia.download}` ({e.nb_telechargements})")
        if e.source:
            st = e.source.conteneur.stat()
            infos = infos_membres(e.source)
            lignes += [f"- Retenu : `{e.source.conteneur.name}` — {_date(st.st_mtime)} — {_taille(st.st_size)}",
                       f"- Source : {len(infos)} fichier(s), {_taille(sum(i[1] for i in infos))}"
                       + (f", en-tête « {ia.entete} »" if ia.entete else "")]
        lignes.append(f"- Sortie : `{ia.sortie}` — "
                      + (f"{e.nb_md} fichiers .md" if e.nb_md is not None else "dossier absent"))
        if e.source:
            lignes += [""] + tableau(["Fichier", "Taille", "Date"], "<><",
                                     [(nom, _taille(taille), date) for nom, taille, date in infos])
        if e.doublons:
            entete = "**Titres en double**" if e.doublons_anomalie else "Titres en double (titres non modifiables)"
            lignes.append(f"- {entete} :")
            for d in e.doublons:
                dates = ", ".join(c["creee"] for c in d["conversations"])
                titres = {_court(c["titre"]) for c in d["conversations"]}
                variantes = "" if len(titres) == 1 else " — titres : " + " / ".join(sorted(titres))
                lignes.append(f"  - {d['nom']} — {len(d['conversations'])} conversations : {dates}{variantes}")
            if e.doublons_anomalie:
                anomalies.append(f"{ia.nom} : {len(e.doublons)} titre(s) en double à renommer")
        if e.interrompu:
            lignes.append(f"- **Génération interrompue** : `{ia.sortie / TEMP}` présent")
            anomalies.append(f"{ia.nom} : génération interrompue")
        if e.etat in (SOURCE_INTROUVABLE, LECTEUR_INTROUVABLE):
            anomalies.append(f"{ia.nom} : {e.detail}")
        lignes.append("")

    for nom in dossiers_non_declares(config["racines"]["downloads"], [e.ia for e in evals]):
        anomalies.append(f"dossier de téléchargement non déclaré : {nom}")

    a_traiter = [e.ia.nom for e in evals if e.etat in A_TRAITER]
    lignes += ["## Synthèse", "",
               f"- {len(evals)} IA déclarées, {sum(e.nb_md or 0 for e in evals)} fichiers .md en sortie",
               f"- À traiter : {', '.join(a_traiter)}" if a_traiter else "- Tout est à jour"]
    anomalies = ANOMALIES_SEANCE + anomalies
    lignes += [f"- {a}" for a in anomalies] if anomalies else ["- Aucune anomalie"]
    RAPPORT.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return RAPPORT, bool(anomalies or ANOMALIES_SEANCE)


# ---------- raccourci ----------

RACCOURCI = ICI / "ia2md.lnk"


def _ps(texte: str) -> str:
    """Chaîne littérale PowerShell (apostrophes doublées)."""
    return "'" + str(texte).replace("'", "''") + "'"


def commande_raccourci(python: Path) -> str:
    return ("$r = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps(RACCOURCI) + "); "
            f"$r.TargetPath = {_ps(python)}; "
            f"$r.Arguments = {_ps(chr(34) + str(Path(__file__).resolve()) + chr(34))}; "
            f"$r.WorkingDirectory = {_ps(ICI)}; "
            "$r.Description = 'ia2md : superviseur des exports d''IA'; "
            "$r.Save()")


def creer_raccourci() -> None:
    """Crée ia2md.lnk dans le dossier du superviseur s'il est absent.

    Vise l'interpréteur principal (pas le lanceur py.exe ni celui d'un environnement
    virtuel, qui relancent Python comme processus enfant et empêcheraient la pause finale).
    Après un changement de version de Python : supprimer ia2md.lnk, il sera recréé.
    """
    if os.name != "nt" or RACCOURCI.exists():
        return
    python = Path(getattr(sys, "_base_executable", None) or sys.executable)
    if python.name.lower() == "pythonw.exe":  # la version sans console ne convient pas
        python = python.with_name("python.exe")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", commande_raccourci(python)],
                       check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as ex:
        print(couleur(f"Raccourci non créé : {ex}", ROUGE))
        return
    msg = f"Raccourci créé : `{RACCOURCI}` → `{python}`"
    print(msg.replace("`", ""))
    SEANCE.append(msg)


# ---------- programme ----------

def main() -> None:
    config, ias = charger_definitions()
    print(f"\nia2md {VERSION}")
    creer_raccourci()
    ranger_arrivees(config["arrivee"], ias)
    evals = [evaluer(ia) for ia in ias]
    afficher(evals)

    if FORCER_JOURNAL:
        print("\nFORCER_JOURNAL actif : journaux écrits sans génération.")
        for e in evals:
            if e.source:
                ecrire_journal(e.ia, e.source, e.nb_md or 0)
                print(f"  journal forcé : {e.ia.nom}")
    else:
        for e in demander(evals):
            generer(e, config["utilisateur"])

    evals = [evaluer(ia) for ia in ias]  # état après génération
    rapport, anomalie = ecrire_rapport(evals, config)
    print(f"\nRapport : {rapport}")
    if anomalie:
        print(couleur("Anomalie : ouverture du rapport.", ROUGE))
        if os.name == "nt":
            os.startfile(rapport)


if __name__ == "__main__":
    try:
        main()
    finally:
        if console_dediee():
            input("\nEntrée pour fermer…")
