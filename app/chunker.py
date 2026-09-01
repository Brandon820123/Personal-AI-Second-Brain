"""Split document text into overlapping, paragraph-aware chunks."""

import re


DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 100


def _take_text_part(text, maximum_length):
    """Take one text part, preferring a whitespace boundary."""
    if len(text) <= maximum_length:
        return text.strip(), ""

    possible_breaks = list(re.finditer(r"\s+", text[: maximum_length + 1]))
    break_position = possible_breaks[-1].start() if possible_breaks else maximum_length

    return text[:break_position].strip(), text[break_position:].strip()


def _get_overlap_text(text, overlap):
    """Return up to roughly ``overlap`` ending characters at a word boundary."""
    if overlap == 0:
        return ""

    if len(text) <= overlap:
        return text

    start = len(text) - overlap

    if start > 0 and not text[start - 1].isspace() and not text[start].isspace():
        while start < len(text) and not text[start].isspace():
            start += 1

    return text[start:].strip()


def _split_long_paragraph(paragraph, chunk_size, overlap):
    """Split one long paragraph with overlap only between its own parts."""
    first_part, remaining_text = _take_text_part(paragraph, chunk_size)
    parts = [first_part]

    while remaining_text:
        available_overlap = min(overlap, max(0, chunk_size - 2))
        overlap_text = _get_overlap_text(parts[-1], available_overlap)
        separator = " " if overlap_text else ""
        available_length = chunk_size - len(overlap_text) - len(separator)
        new_text, remaining_text = _take_text_part(
            remaining_text,
            available_length,
        )
        parts.append(f"{overlap_text}{separator}{new_text}")

    return parts


def chunk_text(
    text,
    chunk_size=DEFAULT_CHUNK_SIZE,
    overlap=DEFAULT_OVERLAP,
):
    """Return text chunks in their original order.

    Chunk sizes and overlap are measured in characters. Paragraphs are kept together
    when they fit, while unusually long paragraphs are split into smaller parts.
    """
    if chunk_size <= 0:
        raise ValueError("Chunk size must be greater than zero.")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("Overlap must be zero or more and smaller than chunk size.")

    if not text or not text.strip():
        return []

    raw_paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    paragraphs = []
    paragraph_index = 0

    while paragraph_index < len(raw_paragraphs):
        paragraph = raw_paragraphs[paragraph_index]
        is_markdown_heading = re.fullmatch(r"#{1,6}\s+.+", paragraph)

        if is_markdown_heading and paragraph_index + 1 < len(raw_paragraphs):
            paragraph = f"{paragraph}\n\n{raw_paragraphs[paragraph_index + 1]}"
            paragraph_index += 1

        paragraphs.append(paragraph)
        paragraph_index += 1

    chunks = []
    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            chunks.append(paragraph)
        else:
            chunks.extend(
                _split_long_paragraph(paragraph, chunk_size, overlap)
            )

    return chunks


def chunk_document_pages(
    document_pages,
    chunk_size=DEFAULT_CHUNK_SIZE,
    overlap=DEFAULT_OVERLAP,
):
    """Chunk document sections without combining text from separate PDF pages."""
    chunks = []
    chunk_metadata = []

    for document_page in document_pages:
        page_chunks = chunk_text(
            document_page["text"],
            chunk_size=chunk_size,
            overlap=overlap,
        )

        for page_chunk in page_chunks:
            chunks.append(page_chunk)
            chunk_metadata.append(
                {"page_number": document_page.get("page_number")}
            )

    return chunks, chunk_metadata
