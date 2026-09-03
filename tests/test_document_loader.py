"""Tests for loading local text documents."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.document_loader import (
    PDF_NO_TEXT_MESSAGE,
    load_document,
    load_document_pages,
)


def add_text_page(writer, text):
    """Add one simple text page suitable for local extraction tests."""
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    content = DecodedStreamObject()
    escaped_text = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    content.set_data(
        f"BT /F1 12 Tf 72 720 Td ({escaped_text}) Tj ET".encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(content)


def write_pdf(path, page_texts):
    """Write a small local PDF, using None for a page without text."""
    writer = PdfWriter()

    for page_text in page_texts:
        if page_text is None:
            writer.add_blank_page(width=612, height=792)
        else:
            add_text_page(writer, page_text)

    with path.open("wb") as pdf_file:
        writer.write(pdf_file)


class DocumentLoaderTests(unittest.TestCase):
    """Check supported documents and clear loading errors."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_loads_txt_file(self):
        document = self.directory / "notes.txt"
        document.write_text("Private local notes.", encoding="utf-8")

        self.assertEqual(load_document(document), "Private local notes.")

    def test_loads_markdown_file(self):
        document = self.directory / "notes.md"
        document.write_text("# Heading\n\nMarkdown text.", encoding="utf-8")

        self.assertEqual(load_document(document), "# Heading\n\nMarkdown text.")

    def test_extracts_docx_paragraphs_without_office_or_cloud_services(self):
        document = self.directory / "lesson.docx"
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Calculus introduction.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Limits and derivatives.</w:t></w:r></w:p>
  </w:body>
</w:document>"""

        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", document_xml)

        self.assertEqual(
            load_document_pages(document),
            [
                {
                    "text": "Calculus introduction.\n\nLimits and derivatives.",
                    "page_number": None,
                }
            ],
        )

    def test_extracts_pdf_text_with_page_numbers(self):
        document = self.directory / "physics.pdf"
        write_pdf(
            document,
            ["Page one explains inertia.", "Page two explains gravity."],
        )

        pages = load_document_pages(document)

        self.assertEqual(
            pages,
            [
                {"text": "Page one explains inertia.", "page_number": 1},
                {"text": "Page two explains gravity.", "page_number": 2},
            ],
        )
        self.assertEqual(
            load_document(document),
            "Page one explains inertia.\n\nPage two explains gravity.",
        )

    def test_preserves_page_number_after_a_blank_pdf_page(self):
        document = self.directory / "notes.pdf"
        write_pdf(document, [None, "Text appears on page two."])

        self.assertEqual(
            load_document_pages(document),
            [{"text": "Text appears on page two.", "page_number": 2}],
        )

    def test_rejects_pdf_without_extractable_text(self):
        document = self.directory / "scanned.pdf"
        write_pdf(document, [None, None])

        with self.assertRaisesRegex(ValueError, "OCR support is not implemented"):
            load_document(document)

        self.assertIn("does not contain extractable text", PDF_NO_TEXT_MESSAGE)

    def test_rejects_missing_file(self):
        missing_document = self.directory / "missing.txt"

        with self.assertRaisesRegex(FileNotFoundError, "Document not found"):
            load_document(missing_document)

    def test_rejects_unsupported_extension(self):
        document = self.directory / "notes.rtf"
        document.write_text("Not supported.", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Unsupported document type"):
            load_document(document)

    def test_rejects_non_utf8_text(self):
        document = self.directory / "notes.txt"
        document.write_bytes(b"\xff\xfe")

        with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
            load_document(document)


if __name__ == "__main__":
    unittest.main()
