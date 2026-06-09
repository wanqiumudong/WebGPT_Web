from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from Rag_Framework.text_processing import (
    ExtractedPdfDocument,
    ExtractedTextBlock,
    SectionMetadata,
    chunk_pdf_document,
)
try:
    from Rag_Framework.query_rewriter import parse_rewrite_content
except ModuleNotFoundError:  # pragma: no cover
    parse_rewrite_content = None

try:
    from Rag_Framework.text_runtime import format_reranker_instruction
except ModuleNotFoundError:  # pragma: no cover - optional dependency in light test envs
    format_reranker_instruction = None


class TextRagPipelineTest(unittest.TestCase):
    def test_chunk_pdf_document_prefers_toc_metadata(self) -> None:
        document = ExtractedPdfDocument(
            file_path="/tmp/fake.pdf",
            page_count=100,
            body_font_size=11.0,
            blocks=(
                ExtractedTextBlock(
                    block_index=0,
                    page_num=1,
                    text="Chapter 1 Introduction",
                    font_size=24.0,
                    is_bold=True,
                    line_count=1,
                ),
                ExtractedTextBlock(
                    block_index=1,
                    page_num=1,
                    text="A" * 420,
                    font_size=11.0,
                    is_bold=False,
                    line_count=4,
                ),
                ExtractedTextBlock(
                    block_index=2,
                    page_num=2,
                    text="1.1 Device Contacts",
                    font_size=18.0,
                    is_bold=True,
                    line_count=1,
                ),
                ExtractedTextBlock(
                    block_index=3,
                    page_num=2,
                    text="B" * 520,
                    font_size=11.0,
                    is_bold=False,
                    line_count=5,
                ),
            ),
            toc_sections=(
                SectionMetadata("Chapter 1 Introduction", "Chapter 1 Introduction", 1, 1),
                SectionMetadata(
                    "1.1 Device Contacts",
                    "Chapter 1 Introduction > 1.1 Device Contacts",
                    2,
                    2,
                ),
                SectionMetadata("Chapter 2 Mesh", "Chapter 2 Mesh", 1, 30),
                SectionMetadata("Chapter 3 Doping", "Chapter 3 Doping", 1, 60),
                SectionMetadata("Appendix", "Appendix", 1, 90),
            ),
        )

        chunks = chunk_pdf_document(document, target_chars=600, soft_max_chars=900)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].section_title, "Chapter 1 Introduction")
        self.assertEqual(chunks[0].page_num_start, 1)
        self.assertEqual(chunks[0].page_num_end, 1)
        self.assertEqual(chunks[1].section_title, "1.1 Device Contacts")
        self.assertEqual(
            chunks[1].section_path,
            "Chapter 1 Introduction > 1.1 Device Contacts",
        )
        self.assertEqual(chunks[1].section_level, 2)

    def test_chunk_pdf_document_uses_heading_fallback_without_toc(self) -> None:
        document = ExtractedPdfDocument(
            file_path="/tmp/fake.pdf",
            page_count=3,
            body_font_size=10.5,
            blocks=(
                ExtractedTextBlock(
                    block_index=0,
                    page_num=1,
                    text="Process Flow",
                    font_size=18.0,
                    is_bold=True,
                    line_count=1,
                ),
                ExtractedTextBlock(
                    block_index=1,
                    page_num=1,
                    text=" ".join(["implant anneal spacer gate"] * 40),
                    font_size=10.5,
                    is_bold=False,
                    line_count=5,
                ),
                ExtractedTextBlock(
                    block_index=2,
                    page_num=2,
                    text="2 Mesh Strategy",
                    font_size=17.0,
                    is_bold=True,
                    line_count=1,
                ),
                ExtractedTextBlock(
                    block_index=3,
                    page_num=2,
                    text=" ".join(["channel oxide interface refinement"] * 35),
                    font_size=10.5,
                    is_bold=False,
                    line_count=4,
                ),
            ),
            toc_sections=(),
        )

        chunks = chunk_pdf_document(document, target_chars=500, soft_max_chars=800)

        self.assertGreaterEqual(len(chunks), 2)
        section_titles = {chunk.section_title for chunk in chunks}
        self.assertIn("Process Flow", section_titles)
        self.assertIn("2 Mesh Strategy", section_titles)
        process_chunks = [chunk for chunk in chunks if chunk.section_title == "Process Flow"]
        mesh_chunks = [chunk for chunk in chunks if chunk.section_title == "2 Mesh Strategy"]
        self.assertTrue(all(chunk.section_path == "Process Flow" for chunk in process_chunks))
        self.assertTrue(all(chunk.section_level == 1 for chunk in mesh_chunks))

    def test_chunk_pdf_document_splits_oversized_block_with_sentence_boundary(self) -> None:
        long_text = " ".join(
            f"Sentence {index} explains contact placement and mesh refinement."
            for index in range(1, 60)
        )
        document = ExtractedPdfDocument(
            file_path="/tmp/fake.pdf",
            page_count=1,
            body_font_size=11.0,
            blocks=(
                ExtractedTextBlock(
                    block_index=0,
                    page_num=1,
                    text="Contact Definition",
                    font_size=18.0,
                    is_bold=True,
                    line_count=1,
                ),
                ExtractedTextBlock(
                    block_index=1,
                    page_num=1,
                    text=long_text,
                    font_size=11.0,
                    is_bold=False,
                    line_count=12,
                ),
            ),
            toc_sections=(),
        )

        chunks = chunk_pdf_document(document, target_chars=500, soft_max_chars=700)

        self.assertGreaterEqual(len(chunks), 3)
        self.assertTrue(all(chunk.section_title == "Contact Definition" for chunk in chunks))
        self.assertTrue(all("Sentence" in chunk.text_content for chunk in chunks))
        self.assertTrue(all(chunk.page_num_start == 1 and chunk.page_num_end == 1 for chunk in chunks))

    def test_format_reranker_instruction_uses_expected_layout(self) -> None:
        if format_reranker_instruction is None:
            self.skipTest("text_runtime optional dependencies are not installed")
        prompt = format_reranker_instruction(
            instruction="Given a web search query, retrieve relevant passages that answer the query",
            query="What is the capital of China?",
            document="The capital of China is Beijing.",
        )

        self.assertTrue(prompt.startswith("<Instruct>: Given a web search query"))
        self.assertIn("\n<Query>: What is the capital of China?", prompt)
        self.assertIn("\n<Document>: The capital of China is Beijing.", prompt)

    def test_parse_rewrite_content_reads_json_and_deduplicates(self) -> None:
        if parse_rewrite_content is None:
            self.skipTest("query_rewriter optional dependencies are not installed")

        content = """
        {
          "queries": [
            "Sentaurus Structure Editor NMOS example",
            "Sentaurus Structure Editor NMOS example",
            "NMOS SDE contact doping mesh setup"
          ]
        }
        """

        queries = parse_rewrite_content(content, max_items=4)

        self.assertEqual(
            queries,
            [
                "Sentaurus Structure Editor NMOS example",
                "NMOS SDE contact doping mesh setup",
            ],
        )


if __name__ == "__main__":
    unittest.main()
