# ia2md

Converts AI chat exports (ChatGPT, Claude, DeepSeek, Gemini, Google AI Mode, Grok, Perplexity)
into one Markdown file per conversation, for local backup and indexing tools (DocFetcher, NotebookLM).

The owner is a French professional developer: talk to them in French, write everything in the repo in English.
Principles first (short), implementation after; no heavy formatting before a decision is made.

## Runtime

- Windows only in practice (Known Folder API for Downloads, `SetFileTime` for creation dates, `.lnk` shortcut).
  Code paths for other OSes exist only as fallbacks; don't remove the Windows specifics.
- Python 3.11+ (`tomllib`). Third-party dependency: `html2text` (HTML exports only, imported lazily).
- No test suite. A cloud session cannot run the tool on real data; say so instead of claiming it works.

## Architecture

- `ia2md.py`: supervisor. Session = sort arrivals from Downloads into archive folders → evaluate each AI
  (find source in latest download, compare with journal) → generate on user choice → write report.
- `ia2md_commun.py`: shared model (`Tour`, `Conversation`, `Source`), Markdown rendering, transactional export
  (write into `_ia2md_temp`, swap only on success).
- `Readers/<AI>.py`: one reader per AI, contract = `lire(source, user, label)` yielding `Conversation`s and
  `valide(document) -> bool`. Optional module flag `TITRE_MODIFIABLE`.
- `ia2md.toml`: per-AI definitions (source file name or glob, optional shared download folder, header filter).
- Journal `ia2md.json` in each AI output folder: source identity (size + CRC32 per file) and version.
  `VERSION` major.minor change ⇒ every AI is flagged for regeneration; patch change does not.

## Conventions (decided)

- **English everywhere**: identifiers, comments, docstrings, console messages, states, report, generated
  Markdown labels, TOML keys, journal keys. No i18n layer for now (maybe later); don't build one preemptively.
- Keep the existing style: small functions, dataclasses, `pathlib`, type hints, comments only where non-obvious.
- Breaking changes to TOML/journal formats are acceptable: everything is regenerated, no backward-compat
  reading of old journals.

## Roadmap

1. **Translate the code base to English** (in progress / next). Includes the reader contract
   (e.g. `read()` / `is_valid()`), the TOML keys, the journal keys and the Markdown output labels
   (`Créée`, `Mise à jour`, `Sans titre`, attachment markers…). Bump `VERSION` to 2.0.0.
2. **Configuration split** (to clarify with the owner before coding): `[racines]` and `[general]`
   don't belong in `ia2md.toml`, which should only describe the AIs. Paths (downloads, exports,
   arrival) and the user name must come from an interface that asks for them and stores them per machine.
   The `sortie` (output subfolder, default `md-files`) setting is of doubtful use.
3. **Duplicate titles** (`doublons`): secondary feature, likely to be removed. Don't extend it;
   file name collisions are already handled by `chemin_libre` (`name (2).md`).
