from __future__ import annotations

import re
from typing import Dict, List, Optional

MAX_TOPICS_PER_SECTION = 12
FORCED_SECTIONS: tuple[str, ...] = ("Now", "Heads-up", "Context", "その他")
SECTION_ALIAS_MAP: Dict[str, str] = {
    "now": "Now",
    "now続き": "Now",
    "now(続き)": "Now",
    "heads-up": "Heads-up",
    "headsup": "Heads-up",
    "headsup続き": "Heads-up",
    "heads-up(続き)": "Heads-up",
    "context": "Context",
    "context続き": "Context",
    "context(続き)": "Context",
    "その他": "その他",
    "そのた": "その他",
}

MENTION_RE = re.compile(r"言及×(\d+)")
FOOTER_RE = re.compile(r"^（言及×\d+(?: / .*?)?）$")
HEADLINE_SEPARATORS = ("—", "―", "–", " - ", " — ", " ‐ ")

TEXT_REPLACEMENTS = [
    ("バイナンス", "Binance"),
    ("バイナ", "Binance"),
    ("nashinashi133", "ryutaro (nashinashi133)"),
]


def _normalize_section_label(raw: str) -> Optional[str]:
    token = raw.strip().strip('#').strip()
    if not token:
        return None
    stripped = token
    for suffix in ("(続き)", "（続き）", "続き", "(続)"):
        if suffix in stripped:
            stripped = stripped.replace(suffix, "")
    normalized = stripped.replace(" ", "")
    lower = normalized.lower()
    return SECTION_ALIAS_MAP.get(normalized) or SECTION_ALIAS_MAP.get(lower)


def _looks_like_headline(text: str) -> bool:
    if not text or text.startswith('（言及×') or text.startswith('##'):
        return False
    if text.startswith('**') and text.endswith('**'):
        return True
    return any(sep in text for sep in HEADLINE_SEPARATORS)


def _parse_footer(line: str, default_mentions: int = 0) -> tuple[str, int]:
    stripped = line.strip()
    match = MENTION_RE.search(stripped)
    mentions = default_mentions
    if match:
        try:
            mentions = int(match.group(1))
        except ValueError:
            pass
    if not stripped:
        stripped = f"（言及×{mentions}）"
    return stripped, mentions


def _summarize_remainder(topics: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(topics) <= MAX_TOPICS_PER_SECTION:
        return topics
    keep = topics[: max(MAX_TOPICS_PER_SECTION - 1, 1)]
    remainder = topics[max(MAX_TOPICS_PER_SECTION - 1, 1):]
    summary_titles = [item['headline'] for item in remainder[:8]]
    total_mentions = sum(item.get('mention_count', 0) or 0 for item in remainder)
    if summary_titles:
        summary_sentence = "その他の主な話題: " + " / ".join(summary_titles)
    else:
        summary_sentence = "その他の主な話題があります。"
    footer = f"（言及×{total_mentions}）" if total_mentions else "（言及×-）"
    keep.append({
        'headline': 'その他主要トピック',
        'paragraph': summary_sentence,
        'footer': footer,
        'mention_count': total_mentions,
    })
    return keep


def normalize_digest_markdown(markdown: str) -> str:
    text = markdown or ""
    if not text.strip():
        return text
    # always reflow to deduplicate topics, even if markdown already uses headings
    lines = [line.rstrip() for line in text.splitlines() if not line.strip().startswith('```')]
    if not lines:
        return text

    header_line = lines[0].strip()
    if header_line.startswith('### '):
        header_line = header_line[4:].strip()
    elif header_line.startswith('## '):
        header_line = header_line[3:].strip()
    content_lines = lines[1:]

    section_topics: Dict[str, List[Dict[str, str]]] = {}
    section_order: List[str] = []
    current_section: Optional[str] = None
    current_topic: List[str] = []

    def flush_topic() -> None:
        nonlocal current_topic, current_section
        topic_lines = [l.strip() for l in current_topic if l.strip()]
        if not topic_lines:
            current_topic = []
            return
        section = current_section or FORCED_SECTIONS[0]
        if section not in section_topics:
            section_topics[section] = []
            section_order.append(section)
        headline_raw = topic_lines[0]
        headline = headline_raw.strip('* ')
        body_lines: List[str] = []
        footer_line = None
        for entry in topic_lines[1:]:
            if FOOTER_RE.match(entry):
                footer_line = entry
            elif entry:
                body_lines.append(entry)
        footer_line, mentions = _parse_footer(footer_line or '', 0)
        paragraph = " ".join(body_lines)
        section_topics[section].append({
            'headline': headline,
            'paragraph': paragraph,
            'footer': footer_line,
            'mention_count': mentions,
        })
        current_topic = []

    for raw_line in content_lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        section_label = _normalize_section_label(stripped)
        if section_label:
            flush_topic()
            current_section = section_label
            if current_section not in section_topics:
                section_topics[current_section] = []
                section_order.append(current_section)
            continue
        if _looks_like_headline(stripped):
            flush_topic()
            current_topic = [stripped]
            continue
        if not current_topic:
            continue
        current_topic.append(stripped)

    flush_topic()

    for section in FORCED_SECTIONS:
        section_topics.setdefault(section, [])
    extra_sections = [s for s in section_order if s not in FORCED_SECTIONS]

    output_lines: List[str] = []
    global_seen: set[tuple[str, str]] = set()
    header = header_line or "6h Digest"
    if not header.startswith("**"):
        header = f"**{header}**"
    output_lines.append(header)
    output_lines.append("")

    def render_section(name: str, topics: List[Dict[str, str]]) -> None:
        output_lines.append(f"## {name}")
        deduped: List[Dict[str, str]] = []
        seen_headlines: set[str] = set()
        for topic in topics:
            key = topic['headline'].strip('* ').lower()
            if key in seen_headlines:
                continue
            seen_headlines.add(key)
            deduped.append(topic)
        deduped = _summarize_remainder(deduped)
        if not deduped:
            output_lines.append("該当なし")
            output_lines.append("")
            return
        for topic in deduped:
            headline = topic['headline'].strip()
            paragraph = topic.get('paragraph', '').strip()
            key = (headline.strip("* ").lower(), paragraph)
            if key in global_seen:
                continue
            global_seen.add(key)
            if not headline.startswith("**"):
                output_lines.append(f"**{headline}**")
            else:
                output_lines.append(headline)
            if paragraph:
                output_lines.append(paragraph)
            output_lines.append(topic.get('footer', '（言及×-）'))
            output_lines.append("")

    for name in FORCED_SECTIONS:
        render_section(name, section_topics.get(name, []))
    for name in extra_sections:
        render_section(name, section_topics.get(name, []))

    while output_lines and output_lines[-1] == "":
        output_lines.pop()

    output = "\n".join(output_lines)
    for old, new in TEXT_REPLACEMENTS:
        output = output.replace(old, new)
    return output
