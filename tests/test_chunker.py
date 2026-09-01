"""Tests for paragraph-aware text chunking."""

import unittest

from app.chunker import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    chunk_document_pages,
    chunk_text,
)


class ChunkTextTests(unittest.TestCase):
    """Check chunk order, paragraph handling, and overlap."""

    def test_uses_production_character_defaults(self):
        self.assertEqual(DEFAULT_CHUNK_SIZE, 1000)
        self.assertEqual(DEFAULT_OVERLAP, 100)

    def test_keeps_complete_paragraphs_separate(self):
        first_paragraph = "First paragraph stays intact."
        second_paragraph = "Second paragraph stays intact too."
        text = f"{first_paragraph}\n\n{second_paragraph}"

        self.assertEqual(
            chunk_text(text, chunk_size=80, overlap=10),
            [first_paragraph, second_paragraph],
        )

    def test_does_not_mix_unrelated_paragraphs(self):
        gas_paragraph = "Gas laws describe pressure and particle energy."
        python_paragraph = "Python dictionaries store labeled key-value data."
        text = f"{gas_paragraph}\n\n{python_paragraph}"

        chunks = chunk_text(text)

        self.assertEqual(chunks, [gas_paragraph, python_paragraph])
        self.assertFalse(any("Gas laws" in chunk and "Python" in chunk for chunk in chunks))

    def test_chunks_remain_in_original_order(self):
        text = (
            "alpha one two three\n\n"
            "beta four five six\n\n"
            "gamma seven eight nine"
        )

        chunks = chunk_text(text, chunk_size=35, overlap=8)

        self.assertIn("alpha", chunks[0])
        self.assertIn("beta", chunks[1])
        self.assertIn("gamma", chunks[2])

    def test_adds_overlap_only_within_a_long_paragraph(self):
        text = "one two three four five six seven eight nine ten"

        chunks = chunk_text(text, chunk_size=25, overlap=8)

        self.assertEqual(chunks, [
            "one two three four five",
            "five six seven eight nine",
            "nine ten",
        ])
        self.assertTrue(all(len(chunk) <= 25 for chunk in chunks))

    def test_keeps_markdown_heading_with_its_paragraph(self):
        text = "# First\n\nalpha beta gamma delta\n\n# Second\n\nepsilon zeta"

        chunks = chunk_text(text, chunk_size=35, overlap=5)

        second_heading_chunk = next(chunk for chunk in chunks if "# Second" in chunk)
        self.assertIn("epsilon zeta", second_heading_chunk)

    def test_zero_overlap_does_not_repeat_previous_chunk(self):
        text = "one two three four five six"

        self.assertEqual(
            chunk_text(text, chunk_size=15, overlap=0),
            ["one two three", "four five six"],
        )

    def test_empty_text_returns_no_chunks(self):
        self.assertEqual(chunk_text("  \n\n  "), [])

    def test_chunks_pdf_pages_separately_with_page_metadata(self):
        document_pages = [
            {"text": "First page topic.", "page_number": 1},
            {"text": "Second page topic.", "page_number": 2},
        ]

        chunks, metadata = chunk_document_pages(document_pages)

        self.assertEqual(chunks, ["First page topic.", "Second page topic."])
        self.assertEqual(metadata, [{"page_number": 1}, {"page_number": 2}])


if __name__ == "__main__":
    unittest.main()
