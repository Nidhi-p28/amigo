from __future__ import annotations

import hashlib
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from xml.etree import ElementTree

from .config import KNOWLEDGE_DIR, WORKSPACE_DIR, _is_reparse_point
from .domain import AmigoServiceError
from .services import ActivityService
from .storage import SqliteDatabase, _now


MAX_TEXT_CHARACTERS = 8_000_000
CHUNK_CHARACTERS = 3_000
CHUNK_OVERLAP = 240
SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".html", ".htm", ".xml", ".json", ".log", ".yaml", ".yml", ".pdf", ".docx"}
_TOKEN = re.compile(r"[\w]{2,}", re.UNICODE)
_STOPWORDS = {"about", "after", "again", "also", "and", "are", "because", "been", "before", "being", "between", "could", "does", "from", "have", "into", "just", "more", "most", "not", "only", "other", "please", "should", "that", "their", "there", "these", "they", "this", "those", "through", "what", "when", "where", "which", "while", "with", "would", "your", "you", "tell", "find", "search", "explain", "according"}


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    locator: str = ""


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1
        elif tag.casefold() in {"p", "br", "div", "li", "h1", "h2", "h3", "tr"} and not self.hidden_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag.casefold() in {"p", "div", "li", "h1", "h2", "h3", "tr"} and not self.hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.strip():
            self.parts.append(data)


def _clean_text(value: str) -> str:
    value = value.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_document(path: Path) -> tuple[str, list[ExtractedSection]]:
    """Extract readable text from a supported local document without executing it."""
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(f"The .{suffix.lstrip('.')} format is not supported for text search yet.")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise UnsupportedDocumentError("PDF indexing needs pypdf. Install the listed app requirements, then try again.") from exc
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                if reader.decrypt("") == 0:
                    raise UnsupportedDocumentError("This PDF is password-protected and cannot be indexed.")
            except Exception as exc:
                if isinstance(exc, UnsupportedDocumentError):
                    raise
                raise UnsupportedDocumentError("This PDF is password-protected and cannot be indexed.") from exc
        sections = []
        for number, page in enumerate(reader.pages, 1):
            text = _clean_text(page.extract_text() or "")
            if text:
                sections.append(ExtractedSection(text, f"p. {number}"))
        if not sections:
            raise AmigoServiceError("No selectable text was found. Image-only PDFs need OCR, which is not part of this phase.")
        return "pypdf", sections
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as package:
                document = ElementTree.fromstring(package.read("word/document.xml"))
        except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise AmigoServiceError("This Word document could not be read.") from exc
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in document.findall(".//w:p", namespace):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
            if text.strip():
                paragraphs.append(text)
        return "docx-xml", [ExtractedSection(_clean_text("\n".join(paragraphs)))]
    if suffix in {".html", ".htm"}:
        parser = _VisibleTextParser()
        parser.feed(_decode_bytes(path.read_bytes()))
        return "html-parser", [ExtractedSection(_clean_text("".join(parser.parts)))]
    if suffix == ".xml":
        try:
            root = ElementTree.fromstring(path.read_bytes())
        except ElementTree.ParseError as exc:
            raise AmigoServiceError("This XML document could not be read.") from exc
        return "xml-text", [ExtractedSection(_clean_text("\n".join(part for part in root.itertext() if part.strip())))]
    return "plain-text", [ExtractedSection(_clean_text(_decode_bytes(path.read_bytes())))]


class UnsupportedDocumentError(AmigoServiceError):
    pass


def _chunks(sections: Iterable[ExtractedSection]) -> list[tuple[int, str, str]]:
    result: list[tuple[int, str, str]] = []
    for section in sections:
        text = _clean_text(section.text)
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + CHUNK_CHARACTERS, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + CHUNK_CHARACTERS // 2, end)
                if boundary < 0:
                    boundary = text.rfind(". ", start + CHUNK_CHARACTERS // 2, end)
                    if boundary >= 0:
                        boundary += 1
                if boundary > start:
                    end = boundary
            body = text[start:end].strip()
            if body:
                result.append((len(result), body, section.locator))
            if end >= len(text):
                break
            start = max(end - CHUNK_OVERLAP, start + 1)
    return result


def _safe_root(path: Path, label: str) -> Path:
    path = Path(path)
    if path.exists() and _is_reparse_point(path):
        raise AmigoServiceError(f"Amigo's {label} folder cannot be a symbolic link or junction.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        if _is_reparse_point(path):
            raise AmigoServiceError(f"Amigo's {label} folder cannot be a symbolic link or junction.")
        return path.resolve(strict=True)
    except OSError as exc:
        raise AmigoServiceError(f"Amigo's {label} folder is unavailable: {exc}") from exc


def _safe_relative_file(root: Path, relative: str, label: str) -> Path:
    portable = str(relative or "").replace("\\", "/")
    posix, windows = PurePosixPath(portable), PureWindowsPath(str(relative or ""))
    parts = [part for part in posix.parts if part not in {"", "."}]
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root or not parts:
        raise AmigoServiceError(f"The registered {label} path is invalid.")
    if any(part == ".." or ":" in part for part in parts):
        raise AmigoServiceError(f"The registered {label} path is outside Amigo's managed folder.")
    target = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if current.exists() and _is_reparse_point(current):
            raise AmigoServiceError(f"Linked paths are blocked in Amigo's {label} folder.")
    resolved = target.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AmigoServiceError(f"The registered {label} path is outside Amigo's managed folder.") from exc
    if not resolved.is_file():
        raise AmigoServiceError(f"The registered {label} is no longer a file.")
    return resolved


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:72]
    return result or "note"


class KnowledgeService:
    """Managed Markdown notes, document indexing, and local FTS5 retrieval."""

    MAX_NOTE_CHARACTERS = 500_000

    def __init__(
        self,
        database: SqliteDatabase | Path | None = None,
        *,
        knowledge_root: Path | None = None,
        materials_root: Path | None = None,
    ) -> None:
        self.db = database if isinstance(database, SqliteDatabase) else SqliteDatabase(database) if database is not None else SqliteDatabase()
        self.knowledge_root = Path(knowledge_root or KNOWLEDGE_DIR)
        self.materials_root = Path(materials_root or WORKSPACE_DIR)

    def list_materials(self) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT m.*, s.status AS index_status, s.indexed_at, s.error_message,
                          GROUP_CONCAT(DISTINCT t.name) AS topic_names
                   FROM materials m
                   LEFT JOIN material_index_state s ON s.material_id = m.id
                   LEFT JOIN material_topics mt ON mt.material_id = m.id
                   LEFT JOIN topics t ON t.id = mt.topic_id
                   GROUP BY m.id ORDER BY m.title COLLATE NOCASE"""
            ).fetchall()
        return [dict(row) for row in rows]

    def list_notes(self) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT n.*, t.name AS topic_name FROM knowledge_notes n
                   JOIN topics t ON t.id = n.topic_id ORDER BY n.updated_at DESC, n.title COLLATE NOCASE"""
            ).fetchall()
        return [dict(row) for row in rows]

    def create_note(self, title: str, topic_name: str, content: str = "") -> dict[str, Any]:
        return self.save_note(title, topic_name, content)

    def save_note(self, title: str, topic_name: str, content: str, note_id: str | None = None) -> dict[str, Any]:
        title = str(title or "").strip()
        topic_name = str(topic_name or "").strip()
        content = str(content or "").strip()
        if not title or len(title) > 200:
            raise AmigoServiceError("Note title is required and must be 200 characters or fewer.")
        if not topic_name or len(topic_name) > 200:
            raise AmigoServiceError("Choose a topic for this note (up to 200 characters).")
        if len(content) > self.MAX_NOTE_CHARACTERS:
            raise AmigoServiceError("A note can contain up to 500,000 characters.")
        root = _safe_root(self.knowledge_root, "Knowledge")
        timestamp = _now()
        new_id = note_id or str(uuid.uuid4())
        with self.db.connection() as connection:
            topic_id = ActivityService._get_or_create_topic(connection, topic_name)
            current = connection.execute("SELECT * FROM knowledge_notes WHERE id = ?", (new_id,)).fetchone()
            if note_id and current is None:
                raise AmigoServiceError("That note no longer exists.")
            if current:
                relative_path = str(current["relative_path"])
                created_at = str(current["created_at"])
            else:
                folder = _slug(topic_name)
                stem = _slug(title)
                relative_path = f"{folder}/{stem}.md"
                counter = 2
                while (root / relative_path).exists():
                    relative_path = f"{folder}/{stem}-{counter}.md"
                    counter += 1
                created_at = timestamp
            target = _safe_create_target(root, relative_path)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        _atomic_write_text(target, content + ("\n" if content else ""))
        try:
            with self.db.connection() as connection:
                connection.execute(
                    """INSERT INTO knowledge_notes(id, topic_id, title, relative_path, content_sha256, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET topic_id=excluded.topic_id, title=excluded.title,
                       content_sha256=excluded.content_sha256, updated_at=excluded.updated_at""",
                    (new_id, topic_id, title, relative_path, digest, created_at, timestamp),
                )
                connection.execute("DELETE FROM knowledge_chunks WHERE note_id = ?", (new_id,))
                for part_no, body, locator in _chunks([ExtractedSection(content)]):
                    connection.execute(
                        "INSERT INTO knowledge_chunks(note_id, topic_id, part_no, title, body, source_path, source_locator) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (new_id, topic_id, part_no, title, body, relative_path, locator),
                    )
        except Exception:
            raise
        return {"id": new_id, "topic_id": topic_id, "topic_name": topic_name, "title": title,
                "relative_path": relative_path, "content_sha256": digest, "created_at": created_at, "updated_at": timestamp}

    def read_note(self, note_id: str) -> dict[str, Any]:
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT n.*, t.name AS topic_name FROM knowledge_notes n JOIN topics t ON t.id = n.topic_id WHERE n.id = ?""",
                (note_id,),
            ).fetchone()
        if not row:
            raise AmigoServiceError("That note no longer exists.")
        root = _safe_root(self.knowledge_root, "Knowledge")
        path = _safe_relative_file(root, row["relative_path"], "Knowledge note")
        content = _decode_bytes(path.read_bytes()).strip()
        if len(content) > self.MAX_NOTE_CHARACTERS:
            raise AmigoServiceError("This note is larger than Amigo's supported 500,000 characters.")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != row["content_sha256"]:
            self._replace_note_chunks(note_id, str(row["topic_id"]), str(row["title"]), str(row["relative_path"]), content)
            with self.db.connection() as connection:
                connection.execute("UPDATE knowledge_notes SET content_sha256 = ?, updated_at = ? WHERE id = ?", (digest, _now(), note_id))
        return {**dict(row), "content": content}

    def _replace_note_chunks(self, note_id: str, topic_id: str, title: str, relative_path: str, content: str) -> None:
        with self.db.connection() as connection:
            connection.execute("DELETE FROM knowledge_chunks WHERE note_id = ?", (note_id,))
            for part_no, body, locator in _chunks([ExtractedSection(content)]):
                connection.execute(
                    "INSERT INTO knowledge_chunks(note_id, topic_id, part_no, title, body, source_path, source_locator) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (note_id, topic_id, part_no, title, body, relative_path, locator),
                )

    def index_material(self, material_id: str, *, force: bool = False) -> dict[str, Any]:
        with self.db.connection() as connection:
            material = connection.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        if not material:
            raise AmigoServiceError("That registered material no longer exists.")
        root = _safe_root(self.materials_root, "Materials")
        source = _safe_relative_file(root, str(material["relative_path"]), "material")
        digest_state = hashlib.sha256()
        with source.open("rb") as reader:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                digest_state.update(block)
        digest = digest_state.hexdigest()
        with self.db.connection() as connection:
            state = connection.execute("SELECT * FROM material_index_state WHERE material_id = ?", (material_id,)).fetchone()
            if state and state["status"] == "indexed" and state["source_sha256"] == digest and not force:
                return dict(state)
            connection.execute(
                """INSERT INTO material_index_state(material_id, status, source_sha256, error_message)
                   VALUES (?, 'indexing', ?, '') ON CONFLICT(material_id) DO UPDATE SET status='indexing', source_sha256=excluded.source_sha256, error_message=''""",
                (material_id, digest),
            )
        try:
            extractor, sections = extract_document(source)
            total_characters = sum(len(section.text) for section in sections)
            if total_characters > MAX_TEXT_CHARACTERS:
                raise AmigoServiceError("This document contains too much extractable text for the current local index (8 million characters maximum).")
            chunks = _chunks(sections)
            if not chunks:
                raise AmigoServiceError("No searchable text was found in this document.")
        except UnsupportedDocumentError as exc:
            with self.db.connection() as connection:
                connection.execute("DELETE FROM knowledge_chunks WHERE material_id = ?", (material_id,))
                connection.execute(
                    """UPDATE material_index_state SET status='unsupported', extractor='', character_count=0,
                       indexed_at=NULL, error_message=? WHERE material_id=?""", (str(exc), material_id)
                )
            return {"material_id": material_id, "status": "unsupported", "error_message": str(exc)}
        except Exception as exc:
            message = str(exc)[:1000] or "Document extraction failed."
            with self.db.connection() as connection:
                connection.execute("DELETE FROM knowledge_chunks WHERE material_id = ?", (material_id,))
                connection.execute(
                    """UPDATE material_index_state SET status='failed', extractor='', character_count=0,
                       indexed_at=NULL, error_message=? WHERE material_id=?""", (message, material_id)
                )
            return {"material_id": material_id, "status": "failed", "error_message": message}
        with self.db.connection() as connection:
            topic_rows = connection.execute(
                "SELECT topic_id FROM material_topics WHERE material_id = ? ORDER BY topic_id", (material_id,)
            ).fetchall()
            topic_id = str(topic_rows[0]["topic_id"]) if topic_rows else None
            connection.execute("DELETE FROM knowledge_chunks WHERE material_id = ?", (material_id,))
            for part_no, body, locator in chunks:
                connection.execute(
                    """INSERT INTO knowledge_chunks(material_id, topic_id, part_no, title, body, source_path, source_locator)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (material_id, topic_id, part_no, str(material["title"]), body, str(material["relative_path"]), locator),
                )
            connection.execute(
                """UPDATE material_index_state SET status='indexed', source_sha256=?, extractor=?, character_count=?,
                   indexed_at=?, error_message='' WHERE material_id=?""",
                (digest, extractor, total_characters, _now(), material_id),
            )
            result = dict(connection.execute("SELECT * FROM material_index_state WHERE material_id = ?", (material_id,)).fetchone())
        return result

    def reindex_all(self) -> list[dict[str, Any]]:
        return [self.index_material(str(row["id"])) for row in self.list_materials()]

    def search(self, query: str, *, limit: int = 8, topic_id: str | None = None) -> list[dict[str, Any]]:
        tokens = [token for token in _TOKEN.findall(str(query or "").casefold()) if token not in _STOPWORDS]
        if not tokens:
            tokens = _TOKEN.findall(str(query or "").casefold())
        tokens = list(dict.fromkeys(tokens))[:12]
        if not tokens:
            return []
        limit = max(1, min(int(limit), 20))
        match_and = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        match_or = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

        def run(match: str) -> list[dict[str, Any]]:
            with self.db.connection() as connection:
                if topic_id:
                    rows = connection.execute(
                        """SELECT c.id, c.note_id, c.material_id, c.title, c.body, c.source_path, c.source_locator,
                                  t.name AS topic_name, snippet(knowledge_fts, 1, '…', '…', ' … ', 36) AS excerpt,
                                  bm25(knowledge_fts, 4.0, 1.0) AS score
                           FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.rowid
                           LEFT JOIN topics t ON t.id=c.topic_id
                           LEFT JOIN material_index_state s ON s.material_id=c.material_id
                           WHERE knowledge_fts MATCH ? AND (c.note_id IS NOT NULL OR s.status='indexed') AND c.topic_id=?
                           ORDER BY score LIMIT ?""", (match, topic_id, limit)
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """SELECT c.id, c.note_id, c.material_id, c.title, c.body, c.source_path, c.source_locator,
                                  t.name AS topic_name, snippet(knowledge_fts, 1, '…', '…', ' … ', 36) AS excerpt,
                                  bm25(knowledge_fts, 4.0, 1.0) AS score
                           FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.rowid
                           LEFT JOIN topics t ON t.id=c.topic_id
                           LEFT JOIN material_index_state s ON s.material_id=c.material_id
                           WHERE knowledge_fts MATCH ? AND (c.note_id IS NOT NULL OR s.status='indexed')
                           ORDER BY score LIMIT ?""", (match, limit)
                    ).fetchall()
            return [dict(row) for row in rows]
        found = run(match_and)
        return found or (run(match_or) if len(tokens) > 1 else [])

    def source_path(self, result: dict[str, Any]) -> str:
        relative = str(result.get("source_path") or "")
        if result.get("note_id"):
            return f"Knowledge/{relative.replace(chr(92), '/') }"
        return f"My Materials/{relative.replace(chr(92), '/') }"


def _safe_create_target(root: Path, relative: str) -> Path:
    portable = relative.replace("\\", "/")
    posix, windows = PurePosixPath(portable), PureWindowsPath(relative)
    parts = [part for part in posix.parts if part not in {"", "."}]
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root or not parts or any(part == ".." or ":" in part for part in parts):
        raise AmigoServiceError("The note path is outside Amigo's Knowledge folder.")
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.exists() and _is_reparse_point(current):
            raise AmigoServiceError("Linked folders are blocked in Amigo's Knowledge folder.")
        current.mkdir(exist_ok=True)
    target = current / parts[-1]
    if target.exists() and _is_reparse_point(target):
        raise AmigoServiceError("Linked note files are blocked in Amigo's Knowledge folder.")
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise AmigoServiceError("The note path is outside Amigo's Knowledge folder.") from exc
    return target


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AmigoServiceError(f"Could not save the Markdown note: {exc}") from exc

