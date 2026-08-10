"""Structure-aware text chunking for tenant knowledge indexing."""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"(?m)^(#{1,6}\s+\S.*)$")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def chunk_text(
    text: str,
    *,
    size: int = 1200,
    overlap: int = 150,
) -> list[str]:
    """Split text on headings/paragraphs/sentences, then pack to ~size with overlap.

    Prefer structural boundaries over a blind character window so RAG chunks keep
    section context. Falls back to sliding windows only for oversized atoms.
    """
    normalized = text.replace("\x00", "").strip()
    if not normalized:
        return []
    size = max(200, size)
    overlap = max(0, min(overlap, size // 2))

    sections = _split_sections(normalized)
    packed: list[str] = []
    for section in sections:
        packed.extend(_pack_section(section, size=size, overlap=overlap))
    return packed


def _split_sections(text: str) -> list[str]:
    parts = _HEADING_RE.split(text)
    if len(parts) == 1:
        return _split_paragraphs(text)

    sections: list[str] = []
    preamble = parts[0].strip()
    if preamble:
        sections.extend(_split_paragraphs(preamble))
    for index in range(1, len(parts), 2):
        heading = parts[index].strip()
        body = parts[index + 1].strip() if index + 1 < len(parts) else ""
        block = f"{heading}\n\n{body}".strip() if body else heading
        if block:
            sections.append(block)
    return sections or [text]


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return paragraphs or [text]


def _pack_section(section: str, *, size: int, overlap: int) -> list[str]:
    if len(section) <= size:
        return [section]

    units = _atomic_units(section)
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_window(unit, size=size, overlap=overlap))
            continue
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current.strip())
        if overlap and chunks:
            tail = chunks[-1][-overlap:].lstrip()
            current = f"{tail}\n\n{unit}".strip() if tail else unit
            if len(current) > size:
                current = unit
        else:
            current = unit
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _atomic_units(text: str) -> list[str]:
    paragraphs = _split_paragraphs(text)
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= 400:
            units.append(paragraph)
            continue
        sentences = [part.strip() for part in _SENTENCE_RE.split(paragraph) if part.strip()]
        units.extend(sentences or [paragraph])
    return units


def _window(text: str, *, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
