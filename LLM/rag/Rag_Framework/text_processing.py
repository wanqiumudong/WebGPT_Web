from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Sequence

import fitz


SPACE_PATTERN = re.compile(r"[ \t\r\f\v]+")
MULTI_NEWLINE_PATTERN = re.compile(r"\n{3,}")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?;:])\s+")
HEADING_NUMBER_PATTERN = re.compile(
    r"^(?:chapter|section|appendix|part)?\s*[0-9]+(?:\.[0-9A-Za-z]+)*\b",
    re.IGNORECASE,
)
TOC_SKIP_TITLES = {
    "contents",
    "table of contents",
    "return to front page",
    "front page",
}
MAX_TOC_LEVEL = 3


@dataclass(frozen=True)
class ExtractedTextBlock:
    block_index: int
    page_num: int
    text: str
    font_size: float
    is_bold: bool
    line_count: int


@dataclass(frozen=True)
class SectionMetadata:
    title: str
    path: str
    level: int
    page_num: int


@dataclass(frozen=True)
class ExtractedPdfDocument:
    file_path: str
    page_count: int
    body_font_size: float
    blocks: tuple[ExtractedTextBlock, ...]
    toc_sections: tuple[SectionMetadata, ...]


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    text_content: str
    page_num_start: int
    page_num_end: int
    section_title: str
    section_path: str
    section_level: int
    chunk_order_in_section: int


@dataclass(frozen=True)
class _TextUnit:
    text: str
    page_num_start: int
    page_num_end: int


@dataclass(frozen=True)
class _SectionAnchor:
    block_index: int
    section: SectionMetadata
    skip_source_block: bool


def ensure_pdf_file(file_path: str) -> Path:
    path = Path(file_path).resolve()
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported")
    return path


def extract_pdf_document(file_path: str) -> ExtractedPdfDocument:
    pdf_path = ensure_pdf_file(file_path)
    blocks: List[ExtractedTextBlock] = []
    sampled_font_sizes: List[float] = []
    with fitz.open(str(pdf_path)) as document:
        toc_sections = tuple(_extract_toc_sections(document))
        for page_index, page in enumerate(document, start=1):
            page_dict = page.get_text("dict", sort=True)
            for raw_block in page_dict.get("blocks", []):
                if raw_block.get("type") != 0:
                    continue
                text, font_size, is_bold, line_count = _extract_block_text(raw_block)
                if not text:
                    continue
                blocks.append(
                    ExtractedTextBlock(
                        block_index=len(blocks),
                        page_num=page_index,
                        text=text,
                        font_size=font_size,
                        is_bold=is_bold,
                        line_count=line_count,
                    )
                )
                if len(text) >= 24:
                    sampled_font_sizes.append(font_size)

        body_font_size = _estimate_body_font_size(sampled_font_sizes, blocks)
        return ExtractedPdfDocument(
            file_path=str(pdf_path),
            page_count=len(document),
            body_font_size=body_font_size,
            blocks=tuple(blocks),
            toc_sections=toc_sections,
        )


def chunk_pdf_document(
    document: ExtractedPdfDocument,
    *,
    target_chars: int = 1200,
    soft_max_chars: int = 1600,
    min_chunk_chars: int = 300,
    min_block_chars: int = 10,
    overlap_chars: int = 120,
    fallback_chunk_chars: int = 1000,
) -> List[TextChunk]:
    if target_chars <= 0 or soft_max_chars < target_chars:
        raise ValueError("Invalid target_chars/soft_max_chars")
    if overlap_chars < 0 or overlap_chars >= fallback_chunk_chars:
        raise ValueError("overlap_chars must be in [0, fallback_chunk_chars)")

    toc_sections = list(document.toc_sections)
    use_toc = len(toc_sections) >= max(5, document.page_count // 20)
    anchors = _build_section_anchors(document, toc_sections=toc_sections, use_toc=use_toc)

    default_section_by_page: Dict[int, SectionMetadata] = {}
    if use_toc:
        default_section_by_page = _build_default_sections_by_page(
            toc_sections=toc_sections,
            page_count=document.page_count,
        )

    chunks: List[TextChunk] = []
    units: List[_TextUnit] = []
    current_section = default_section_by_page.get(1, SectionMetadata("", "", 0, 1))
    chunk_index = 0
    section_chunk_order = 0

    for block in document.blocks:
        anchor = anchors.get(block.block_index)
        if anchor is not None:
            if units:
                built = _build_section_chunks(
                    units=units,
                    section=current_section,
                    chunk_index_start=chunk_index,
                    chunk_order_start=section_chunk_order,
                    target_chars=target_chars,
                    soft_max_chars=soft_max_chars,
                    min_chunk_chars=min_chunk_chars,
                    overlap_chars=overlap_chars,
                    fallback_chunk_chars=fallback_chunk_chars,
                )
                chunks.extend(built)
                chunk_index += len(built)
            current_section = anchor.section
            units = []
            section_chunk_order = 0
            if anchor.skip_source_block:
                continue
        elif use_toc:
            page_section = default_section_by_page.get(block.page_num)
            if page_section is not None and page_section.path != current_section.path and units:
                built = _build_section_chunks(
                    units=units,
                    section=current_section,
                    chunk_index_start=chunk_index,
                    chunk_order_start=section_chunk_order,
                    target_chars=target_chars,
                    soft_max_chars=soft_max_chars,
                    min_chunk_chars=min_chunk_chars,
                    overlap_chars=overlap_chars,
                    fallback_chunk_chars=fallback_chunk_chars,
                )
                chunks.extend(built)
                chunk_index += len(built)
                units = []
                section_chunk_order = 0
                current_section = page_section
            elif page_section is not None and current_section.path == "" and page_section.path:
                current_section = page_section

        block_units = _split_block_to_units(
            block=block,
            soft_max_chars=soft_max_chars,
            fallback_chunk_chars=fallback_chunk_chars,
            overlap_chars=overlap_chars,
        )
        for unit in block_units:
            if len(unit.text) >= min_block_chars:
                units.append(unit)

    if units:
        built = _build_section_chunks(
            units=units,
            section=current_section,
            chunk_index_start=chunk_index,
            chunk_order_start=section_chunk_order,
            target_chars=target_chars,
            soft_max_chars=soft_max_chars,
            min_chunk_chars=min_chunk_chars,
            overlap_chars=overlap_chars,
            fallback_chunk_chars=fallback_chunk_chars,
        )
        chunks.extend(built)

    return chunks


def _extract_toc_sections(document: fitz.Document) -> List[SectionMetadata]:
    raw_toc = document.get_toc(simple=False)
    sections: List[SectionMetadata] = []
    title_stack: List[str] = []
    for entry in raw_toc:
        if len(entry) < 3:
            continue
        level = int(entry[0])
        title = normalize_text(str(entry[1]))
        page_num = max(1, min(int(entry[2]), len(document)))
        if level > MAX_TOC_LEVEL:
            continue
        if not title or title.casefold() in TOC_SKIP_TITLES:
            continue
        while len(title_stack) >= level:
            title_stack.pop()
        title_stack.append(title)
        sections.append(
            SectionMetadata(
                title=title,
                path=" > ".join(title_stack),
                level=level,
                page_num=page_num,
            )
        )
    return sections


def _extract_block_text(raw_block: Dict) -> tuple[str, float, bool, int]:
    lines = raw_block.get("lines", [])
    normalized_lines: List[str] = []
    font_sizes: List[float] = []
    bold_flags: List[bool] = []
    for line in lines:
        span_texts: List[str] = []
        for span in line.get("spans", []):
            text = normalize_text(str(span.get("text", "")))
            if not text:
                continue
            span_texts.append(text)
            font_sizes.append(float(span.get("size", 0.0) or 0.0))
            bold_flags.append(_font_is_bold(str(span.get("font", ""))))
        if span_texts:
            normalized_lines.append(" ".join(span_texts).strip())
    text = "\n".join(normalized_lines).strip()
    if not text:
        return "", 0.0, False, 0
    if not font_sizes:
        font_sizes = [12.0]
    return text, max(font_sizes), any(bold_flags), len(normalized_lines)


def _estimate_body_font_size(
    sampled_font_sizes: Sequence[float],
    blocks: Sequence[ExtractedTextBlock],
) -> float:
    if sampled_font_sizes:
        return float(median(sampled_font_sizes))
    if blocks:
        return float(median(block.font_size for block in blocks))
    return 12.0


def _build_default_sections_by_page(
    *,
    toc_sections: Sequence[SectionMetadata],
    page_count: int,
) -> Dict[int, SectionMetadata]:
    default_sections: Dict[int, SectionMetadata] = {}
    toc_index = 0
    current = SectionMetadata("", "", 0, 1)
    for page_num in range(1, page_count + 1):
        while toc_index < len(toc_sections) and toc_sections[toc_index].page_num <= page_num:
            current = toc_sections[toc_index]
            toc_index += 1
        default_sections[page_num] = current
    return default_sections


def _build_section_anchors(
    document: ExtractedPdfDocument,
    *,
    toc_sections: Sequence[SectionMetadata],
    use_toc: bool,
) -> Dict[int, _SectionAnchor]:
    page_toc_map: Dict[int, List[SectionMetadata]] = {}
    for section in toc_sections:
        page_toc_map.setdefault(section.page_num, []).append(section)

    anchors: Dict[int, _SectionAnchor] = {}
    page_blocks: Dict[int, List[ExtractedTextBlock]] = {}
    for block in document.blocks:
        page_blocks.setdefault(block.page_num, []).append(block)

    previous_page_default = SectionMetadata("", "", 0, 1)
    default_section_by_page = (
        _build_default_sections_by_page(toc_sections=toc_sections, page_count=document.page_count)
        if use_toc
        else {}
    )
    synthetic_stack: List[str] = []

    for page_num in sorted(page_blocks):
        blocks = page_blocks[page_num]
        matched_toc_anchor = False
        page_sections = page_toc_map.get(page_num, [])
        for block in blocks:
            matched_section = _match_toc_section(block.text, page_sections)
            if matched_section is not None:
                anchors[block.block_index] = _SectionAnchor(
                    block_index=block.block_index,
                    section=matched_section,
                    skip_source_block=True,
                )
                matched_toc_anchor = True
                continue

            if not use_toc and _looks_like_heading(block, body_font_size=document.body_font_size):
                synthetic_section = _build_synthetic_section(
                    block=block,
                    current_stack=synthetic_stack,
                )
                synthetic_stack = synthetic_section.path.split(" > ") if synthetic_section.path else []
                anchors.setdefault(
                    block.block_index,
                    _SectionAnchor(
                        block_index=block.block_index,
                        section=synthetic_section,
                        skip_source_block=True,
                    ),
                )

        if use_toc and blocks:
            page_default = default_section_by_page.get(page_num, previous_page_default)
            if page_default.path and page_default.path != previous_page_default.path and not matched_toc_anchor:
                first_block = blocks[0]
                anchors.setdefault(
                    first_block.block_index,
                    _SectionAnchor(
                        block_index=first_block.block_index,
                        section=page_default,
                        skip_source_block=False,
                    ),
                )
            previous_page_default = page_default

    return anchors


def _match_toc_section(
    block_text: str,
    page_sections: Sequence[SectionMetadata],
) -> SectionMetadata | None:
    if not page_sections:
        return None
    normalized_block = _canonical_title(block_text)
    for section in page_sections:
        normalized_title = _canonical_title(section.title)
        if not normalized_title:
            continue
        if normalized_block == normalized_title:
            return section
        if normalized_block.startswith(normalized_title) or normalized_title.startswith(normalized_block):
            if min(len(normalized_block), len(normalized_title)) >= 10:
                return section
    return None


def _looks_like_heading(block: ExtractedTextBlock, *, body_font_size: float) -> bool:
    text = normalize_text(block.text)
    if len(text) < 3 or len(text) > 180:
        return False
    if block.line_count > 3:
        return False
    if _looks_like_table_row(text):
        return False
    larger_than_body = block.font_size >= body_font_size + 1.4 or block.font_size >= body_font_size * 1.18
    numbered = bool(HEADING_NUMBER_PATTERN.match(text))
    bold_heading = block.is_bold and block.font_size >= body_font_size + 0.8
    likely_heading = (larger_than_body or bold_heading or numbered) and len(text.split()) <= 14
    if not likely_heading:
        return False
    return True


def _build_synthetic_section(
    *,
    block: ExtractedTextBlock,
    current_stack: Sequence[str],
) -> SectionMetadata:
    title = normalize_text(block.text)
    inferred_level = _infer_heading_level(title)
    stack = list(current_stack)
    while len(stack) >= inferred_level:
        stack.pop()
    stack.append(title)
    return SectionMetadata(
        title=title,
        path=" > ".join(stack),
        level=inferred_level,
        page_num=block.page_num,
    )


def _infer_heading_level(title: str) -> int:
    match = HEADING_NUMBER_PATTERN.match(title)
    if match:
        numeric_part = re.sub(r"^(chapter|section|appendix|part)\s*", "", match.group(0), flags=re.IGNORECASE)
        return max(1, numeric_part.count(".") + 1)
    return 1


def _looks_like_table_row(text: str) -> bool:
    separators = text.count("|") + text.count("\t")
    numeric_tokens = sum(token.replace(".", "", 1).isdigit() for token in text.split())
    return separators >= 2 or numeric_tokens >= 8


def _split_block_to_units(
    *,
    block: ExtractedTextBlock,
    soft_max_chars: int,
    fallback_chunk_chars: int,
    overlap_chars: int,
) -> List[_TextUnit]:
    text = normalize_text(block.text, keep_newlines=True)
    if len(text) <= soft_max_chars:
        return [_TextUnit(text=text, page_num_start=block.page_num, page_num_end=block.page_num)]

    sentences = _split_sentences(text)
    if len(sentences) > 1:
        units = _pack_text_segments(
            segments=sentences,
            page_num=block.page_num,
            soft_max_chars=soft_max_chars,
            overlap_chars=overlap_chars,
        )
        if units:
            return units

    return _slide_text(
        text=text,
        page_num=block.page_num,
        chunk_size=fallback_chunk_chars,
        overlap_chars=overlap_chars,
    )


def _build_section_chunks(
    *,
    units: Sequence[_TextUnit],
    section: SectionMetadata,
    chunk_index_start: int,
    chunk_order_start: int,
    target_chars: int,
    soft_max_chars: int,
    min_chunk_chars: int,
    overlap_chars: int,
    fallback_chunk_chars: int,
) -> List[TextChunk]:
    if not units:
        return []

    chunks: List[TextChunk] = []
    buffer: List[_TextUnit] = []
    buffer_length = 0
    chunk_index = chunk_index_start
    chunk_order = chunk_order_start

    def flush_buffer(next_seed: _TextUnit | None = None) -> None:
        nonlocal buffer, buffer_length, chunk_index, chunk_order
        if not buffer:
            return
        joined_text = "\n\n".join(unit.text for unit in buffer).strip()
        if section.title and not joined_text.lower().startswith(section.title.lower()):
            joined_text = f"{section.title}\n{joined_text}"
        chunks.append(
            TextChunk(
                chunk_index=chunk_index,
                text_content=joined_text,
                page_num_start=min(unit.page_num_start for unit in buffer),
                page_num_end=max(unit.page_num_end for unit in buffer),
                section_title=section.title,
                section_path=section.path,
                section_level=section.level,
                chunk_order_in_section=chunk_order,
            )
        )
        chunk_index += 1
        chunk_order += 1
        overlap_unit = _build_overlap_unit(buffer[-1], overlap_chars=overlap_chars)
        if next_seed is None:
            buffer = []
            buffer_length = 0
            return
        seed_units = [overlap_unit] if overlap_unit is not None else []
        seed_units.append(next_seed)
        buffer = [unit for unit in seed_units if unit.text]
        buffer_length = sum(len(unit.text) for unit in buffer)

    for unit in units:
        addition = len(unit.text) + (2 if buffer else 0)
        should_flush = (
            buffer
            and buffer_length + addition > soft_max_chars
            and buffer_length >= min_chunk_chars
        )
        if should_flush:
            flush_buffer(next_seed=unit)
            continue

        buffer.append(unit)
        buffer_length += addition
        if buffer_length >= target_chars and len(unit.text) > fallback_chunk_chars:
            flush_buffer()

    if buffer:
        flush_buffer()

    return chunks


def _build_overlap_unit(unit: _TextUnit, *, overlap_chars: int) -> _TextUnit | None:
    text = unit.text.strip()
    if not text:
        return None
    if len(text) <= max(220, overlap_chars):
        overlap_text = text
    else:
        overlap_text = text[-overlap_chars:].strip()
    if not overlap_text:
        return None
    return _TextUnit(
        text=overlap_text,
        page_num_start=unit.page_num_end,
        page_num_end=unit.page_num_end,
    )


def _pack_text_segments(
    *,
    segments: Iterable[str],
    page_num: int,
    soft_max_chars: int,
    overlap_chars: int,
) -> List[_TextUnit]:
    units: List[_TextUnit] = []
    buffer = ""
    for segment in segments:
        segment = normalize_text(segment)
        if not segment:
            continue
        candidate = segment if not buffer else f"{buffer} {segment}"
        if buffer and len(candidate) > soft_max_chars:
            units.append(
                _TextUnit(
                    text=buffer,
                    page_num_start=page_num,
                    page_num_end=page_num,
                )
            )
            overlap = buffer[-overlap_chars:].strip() if len(buffer) > overlap_chars else buffer
            buffer = f"{overlap} {segment}".strip() if overlap else segment
        else:
            buffer = candidate
    if buffer:
        units.append(
            _TextUnit(
                text=buffer,
                page_num_start=page_num,
                page_num_end=page_num,
            )
        )
    return units


def _slide_text(
    *,
    text: str,
    page_num: int,
    chunk_size: int,
    overlap_chars: int,
) -> List[_TextUnit]:
    units: List[_TextUnit] = []
    start = 0
    step = chunk_size - overlap_chars
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunk_text = text[start:end].strip()
        if chunk_text:
            units.append(
                _TextUnit(
                    text=chunk_text,
                    page_num_start=page_num,
                    page_num_end=page_num,
                )
            )
        if end >= len(text):
            break
        start += step
    return units


def _split_sentences(text: str) -> List[str]:
    normalized = normalize_text(text, keep_newlines=True)
    parts = SENTENCE_SPLIT_PATTERN.split(normalized)
    return [part.strip() for part in parts if part.strip()]


def _canonical_title(text: str) -> str:
    normalized = normalize_text(text).casefold()
    normalized = re.sub(r"^(chapter|section|appendix|part)\s*", "", normalized)
    normalized = re.sub(r"^[0-9]+(?:\.[0-9A-Za-z]+)*\s*", "", normalized)
    normalized = re.sub(r"[^0-9a-z]+", " ", normalized)
    return normalized.strip()


def _font_is_bold(font_name: str) -> bool:
    lowered = font_name.casefold()
    return any(keyword in lowered for keyword in ("bold", "black", "heavy", "demi"))


def normalize_text(text: str, *, keep_newlines: bool = False) -> str:
    text = (text or "").replace("\u00a0", " ")
    lines = [SPACE_PATTERN.sub(" ", line).strip() for line in text.splitlines()]
    nonempty_lines = [line for line in lines if line]
    if keep_newlines:
        return MULTI_NEWLINE_PATTERN.sub("\n\n", "\n".join(nonempty_lines)).strip()
    return SPACE_PATTERN.sub(" ", " ".join(nonempty_lines)).strip()
