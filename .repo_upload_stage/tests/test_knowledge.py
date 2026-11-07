from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.agent import DesktopAgent, is_local_knowledge_request
from app.knowledge import KnowledgeService, extract_document
from app.services import KnowledgeLinkService
from app.storage import SqliteDatabase


class KnowledgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = SqliteDatabase(self.root / "db" / "amigo.sqlite3", copy_legacy=False)
        self.materials = self.root / "materials"
        self.materials.mkdir()
        self.knowledge_root = self.root / "knowledge"
        self.service = KnowledgeService(
            self.db, knowledge_root=self.knowledge_root, materials_root=self.materials
        )
        self.original_workspace = patch("app.services.WORKSPACE_DIR", self.materials)
        self.original_workspace.start()

    def tearDown(self) -> None:
        self.original_workspace.stop()
        self.temporary.cleanup()

    def _register_text(self, filename: str, title: str, content: str):
        source = self.root / filename
        source.write_text(content, encoding="utf-8")
        return KnowledgeLinkService(self.db).import_material(source, ["Biology"])

    def test_markdown_note_is_saved_and_searchable_with_a_managed_source(self) -> None:
        note = self.service.save_note("Cell notes", "Biology", "Mitochondria produce ATP during cellular respiration.")

        results = self.service.search("mitochondria ATP")

        self.assertEqual(1, len(results))
        self.assertEqual(note["id"], results[0]["note_id"])
        self.assertEqual("Knowledge/biology/cell-notes.md", self.service.source_path(results[0]))
        self.assertEqual("Biology", self.service.read_note(note["id"])["topic_name"])

    def test_material_indexing_and_search_returns_a_local_source_reference(self) -> None:
        material = self._register_text("study.txt", "Study guide", "Chlorophyll absorbs light for photosynthesis in plants.")

        indexed = self.service.index_material(material.id)
        results = self.service.search("chlorophyll photosynthesis")

        self.assertEqual("indexed", indexed["status"])
        self.assertEqual(1, len(results))
        self.assertEqual("My Materials/study.txt", self.service.source_path(results[0]))
        self.assertEqual("plain-text", indexed["extractor"])

    def test_updating_a_note_removes_old_fts_text(self) -> None:
        note = self.service.save_note("Chemistry", "Science", "Catalyst speeds up a reaction.")
        self.service.save_note("Chemistry", "Science", "Activation energy can be reduced.", note["id"])

        self.assertEqual([], self.service.search("catalyst"))
        self.assertEqual(1, len(self.service.search("activation energy")))

    def test_unsupported_material_is_registered_but_not_indexed(self) -> None:
        material = self._register_text("archive.bin", "Archive", "opaque")

        state = self.service.index_material(material.id)

        self.assertEqual("unsupported", state["status"])
        self.assertIn("not supported", state["error_message"])

    def test_html_extractor_omits_script_content(self) -> None:
        path = self.root / "page.html"
        path.write_text("<h1>Plant cells</h1><script>secret token</script><p>Chloroplasts capture light.</p>", encoding="utf-8")

        _extractor, sections = extract_document(path)

        self.assertIn("Chloroplasts", sections[0].text)
        self.assertNotIn("secret token", sections[0].text)

    def test_docx_extractor_reads_paragraph_text(self) -> None:
        path = self.root / "reading.docx"
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Water cycle</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Evaporation moves water upward.</w:t></w:r></w:p></w:body></w:document>"
        )
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("word/document.xml", document_xml)

        extractor, sections = extract_document(path)

        self.assertEqual("docx-xml", extractor)
        self.assertIn("Evaporation", sections[0].text)

    def test_pdf_extractor_records_page_locator(self) -> None:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        path = self.root / "paper.pdf"
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/F1"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): font})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 18 Tf 40 220 Td (A paper passage about photosynthesis) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as output:
            writer.write(output)

        extractor, sections = extract_document(path)

        self.assertEqual("pypdf", extractor)
        self.assertEqual("p. 1", sections[0].locator)
        self.assertIn("photosynthesis", sections[0].text)

    def test_assistant_local_question_uses_retrieval_and_appends_sources(self) -> None:
        self.service.save_note("Photosynthesis", "Biology", "Chlorophyll absorbs light energy in green plants.")
        agent = DesktopAgent(knowledge_service=self.service)
        captured: dict[str, object] = {}

        def fake_ollama(messages, with_tools, on_chunk=None, cancel_event=None):
            captured["messages"] = messages
            return {"message": {"content": "Chlorophyll absorbs light energy [S1]."}}

        agent._ollama_chat = fake_ollama
        answer = agent.respond("What does my note say about chlorophyll?", [])

        self.assertIn("Chlorophyll absorbs light energy [S1]", answer)
        self.assertIn("Sources:\n[S1] Photosynthesis — Knowledge/biology/photosynthesis.md", answer)
        self.assertIn("light energy in green plants", str(captured["messages"]))

    def test_local_knowledge_intent_requires_a_library_question(self) -> None:
        self.assertTrue(is_local_knowledge_request("Summarize my notes about attention."))
        self.assertTrue(is_local_knowledge_request("What does my PDF say about climate?"))
        self.assertFalse(is_local_knowledge_request("Create a new note in my library."))


if __name__ == "__main__":
    unittest.main()
