"""Load text and PDF documents from the local computer."""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}
PDF_NO_TEXT_MESSAGE = (
    "This PDF does not contain extractable text. "
    "OCR support is not implemented yet."
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


def load_document_pages(file_path):
    """Return document text sections with optional PDF page numbers."""
    path = _validate_document_path(file_path)

    if path.suffix.lower() == ".pdf":
        return _load_pdf_pages(path)

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
