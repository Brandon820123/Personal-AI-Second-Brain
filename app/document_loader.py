"""Load supported knowledge documents from the local computer."""

import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
PDF_NO_TEXT_MESSAGE = (
    "This PDF does not contain extractable text. "
    "OCR support is not implemented yet."
)
DOCX_NO_TEXT_MESSAGE = "This DOCX does not contain extractable text."
WORDPROCESSINGML_NAMESPACE = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
)


def _validate_document_path(file_path):
    """Return a validated path for a supported local document."""
    path = Path(file_path).expanduser()

    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")

    if not path.is_file():
        raise ValueError(f"Document path is not a file: {path}")

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        supported_types = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(
            f"Unsupported document type '{path.suffix or '(no extension)'}'. "
            f"Supported types: {supported_types}"
        )

    return path


def _load_pdf_pages(path):
    """Extract non-empty PDF pages locally with one-based page numbers."""
    try:
        reader = PdfReader(path)
        extracted_pages = []

        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""

            if page_text.strip():
                extracted_pages.append(
                    {
                        "text": page_text.strip(),
                        "page_number": page_number,
                    }
                )
    except (OSError, PdfReadError, ValueError) as error:
        raise ValueError(f"Could not read PDF '{path}': {error}") from error

    if not extracted_pages:
        raise ValueError(PDF_NO_TEXT_MESSAGE)

    return extracted_pages


def _load_docx_sections(path):
    """Extract WordprocessingML paragraphs locally without Office automation."""

    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise ValueError(f"Could not read DOCX '{path}': {error}") from error

    namespace = f"{{{WORDPROCESSINGML_NAMESPACE}}}"
    paragraphs = []

    for paragraph in root.iter(f"{namespace}p"):
        fragments = []

        for element in paragraph.iter():
            if element.tag == f"{namespace}t":
                fragments.append(element.text or "")
            elif element.tag == f"{namespace}tab":
                fragments.append("\t")
            elif element.tag in (f"{namespace}br", f"{namespace}cr"):
                fragments.append("\n")

        text = "".join(fragments).strip()

        if text:
            paragraphs.append(text)

    if not paragraphs:
        raise ValueError(DOCX_NO_TEXT_MESSAGE)

    return [{"text": "\n\n".join(paragraphs), "page_number": None}]


def load_document_pages(file_path):
    """Return document text sections with optional PDF page numbers."""
    path = _validate_document_path(file_path)

    if path.suffix.lower() == ".pdf":
        return _load_pdf_pages(path)

    if path.suffix.lower() == ".docx":
        return _load_docx_sections(path)

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"Could not read '{path}' because it is not valid UTF-8 text."
        ) from error
    except OSError as error:
        raise OSError(f"Could not read document '{path}': {error}") from error

    return [{"text": text, "page_number": None}]


def load_document(file_path):
    """Read a supported local document and return its text."""
    pages = load_document_pages(file_path)
    return "\n\n".join(page["text"] for page in pages)
