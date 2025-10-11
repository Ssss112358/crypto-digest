import re
from typing import List

import requests

CHUNK_LIMIT = 1900


def _parse_sections(markdown: str):
    lines = [line.rstrip() for line in markdown.strip().splitlines()]
    if not lines:
        return "", []
    header = lines[0].strip()
    sections = []
    current = None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            if current:
                sections.append(current)
            current = {"header": stripped, "lines": []}
        else:
            if current is None:
                continue
            current["lines"].append(line.rstrip())
    if current:
        sections.append(current)
    if not sections:
        sections = [{"header": "## その他", "lines": [line.rstrip() for line in lines[1:]]}]
    return header, sections


def _split_topics(section_lines: List[str]) -> List[str]:
    topics = []
    current: List[str] = []
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("**") and stripped.endswith("**") and current:
            topics.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        topics.append("\n".join(current).strip())
    filtered = [topic for topic in topics if topic]
    if not filtered:
        return ["該当なし"]
    return filtered


def _split_text_by_length(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    paragraphs = re.split(r"\n{2,}", text)
    current: List[str] = []
    for para in paragraphs:
        candidate = "\n\n".join(current + [para]) if current else para
        if len(candidate) <= limit:
            current.append(para)
        else:
            if current:
                parts.append("\n\n".join(current))
                current = [para]
            else:
                for i in range(0, len(para), max(limit, 1)):
                    parts.append(para[i:i + limit])
                current = []
    if current:
        parts.append("\n\n".join(current))
    return [p for p in parts if p]


def _build_section_blocks(section: dict, body_limit: int) -> List[str]:
    header = section["header"]
    topics = _split_topics(section.get("lines", []))
    blocks: List[str] = []
    prefix = header
    current_lines: List[str] = [prefix]
    for topic in topics:
        topic_chunks = _split_text_by_length(topic, body_limit)
        for chunk in topic_chunks:
            candidate = "\n\n".join(current_lines + [chunk])
            if len(candidate) <= body_limit:
                current_lines.append(chunk)
            else:
                blocks.append("\n\n".join(current_lines))
                header_cont = f"{header} (続き)"
                if len(chunk) > body_limit:
                    for piece in _split_text_by_length(chunk, body_limit):
                        blocks.append(f"{header_cont}\n\n{piece}")
                    current_lines = [header_cont]
                else:
                    current_lines = [header_cont, chunk]
    if current_lines:
        blocks.append("\n\n".join(current_lines))
    return [block.strip() for block in blocks if block.strip()]


def _format_header(base_header: str, index: int, total: int) -> str:
    header = base_header.strip()
    if total <= 1:
        return header
    marker = "6hダイジェスト"
    if header.startswith("**") and header.endswith("**"):
        inner = header[2:-2]
        if marker in inner:
            inner = inner.replace(marker, f"{marker}({index}/{total})", 1)
        else:
            inner = f"{inner} ({index}/{total})"
        if not inner.endswith("…"):
            inner = f"{inner}…"
        return f"**{inner}**"
    if marker in header:
        header = header.replace(marker, f"{marker}({index}/{total})", 1)
    else:
        header = f"{header} ({index}/{total})"
    if not header.endswith("…"):
        header = f"{header}…"
    return header


def _assemble_messages(header_line: str, sections: List[dict]) -> List[str]:
    body_limit = max(400, CHUNK_LIMIT - len(header_line) - 12)
    section_blocks: List[str] = []
    for section in sections:
        section_blocks.extend(_build_section_blocks(section, body_limit))

    messages: List[str] = []
    current_parts: List[str] = []
    current_len = 0
    for block in section_blocks:
        block = block.strip()
        if not block:
            continue
        addition = len(block) + (2 if current_parts else 0)
        if current_parts and current_len + addition > body_limit:
            messages.append("\n\n".join(current_parts))
            current_parts = [block]
            current_len = len(block)
        else:
            current_parts.append(block)
            current_len += addition if current_len else len(block)
    if current_parts:
        messages.append("\n\n".join(current_parts))

    formatted: List[str] = []
    total = len(messages) or 1
    for idx, body in enumerate(messages or [""], start=1):
        header = _format_header(header_line, idx, total)
        content = f"{header}\n\n{body}".strip()
        formatted.append(content)
    return formatted


def post_markdown(webhook_url: str, markdown: str):
    header_line, sections = _parse_sections(markdown)
    chunks = _assemble_messages(header_line or "6hダイジェスト", sections)
    for chunk in chunks:
        response = requests.post(webhook_url, json={"content": chunk}, timeout=30)
        if response.status_code >= 300:
            raise RuntimeError(f"discord webhook {response.status_code}: {response.text[:200]}")
