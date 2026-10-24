"""Candidate validation for the SourceMaster-to-Layer-3 extraction boundary.

This module is deliberately read-only.  It validates model output against the
current SourceMaster and produces deterministic, source-local evidence anchors;
it does not write a preview or a knowledge base.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping


REQUIRED = (
    "candidate_id",
    "source_card_id",
    "source_unit_id",
    "source_card_sha256",
    "semantic_input_sha256",
    "question_key",
    "claim",
    "source_claim",
    "source_evidence_locator",
    "source_blogger",
    "source_type",
    "primary_theme",
    "related_themes",
    "evidence_status",
    "boundary",
    "disposition",
    "disposition_reason",
)
ALLOWED_DISPOSITIONS = {"进入三层", "待人工判断", "仅保留二层"}


class CandidateError(ValueError):
    """A candidate violates a stable, machine-readable contract code."""

    def __init__(self, code: str, detail: str = ""):
        message = code if not detail else f"{code}:{detail}"
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class EvidenceMatch:
    """One exact original-text occurrence, represented by a stable locator."""

    start_line: int
    end_line: int
    claim_sha256: str

    @property
    def locator(self) -> str:
        return (
            f"original:card-lines:{self.start_line}-{self.end_line}:"
            f"sha256:{self.claim_sha256}"
        )


_APPROVED_SUMMARY_TEXT = re.compile(
    r"(?:原博主校对版口播稿|原博主完整转写稿(?:（点击展开）|\(点击展开\))?|"
    r"完整原始口播稿|原始 ASR[^<]*)"
)
_RAW_PRE = re.compile(
    r"<pre\b(?=[^>]*\bdata-lv-know\s*=\s*[\"']raw-transcript[\"'])[^>]*>",
    re.I,
)
_GENERATED_BEGIN = "<!-- GENERATED_RELATIONS_BEGIN -->"
_GENERATED_END = "<!-- GENERATED_RELATIONS_END -->"
_ORDINAL_FRAGMENT = re.compile(
    r"^第[一二三四五六七八九十百千万\d]+(?:个)?(?:点|步|条|部分)?"
    r"(?:呢|[，,]\s*(?:呢|其实|就是)|\s*(?:呢|其实就是|就是))"
)
_LEADING_PRONOUN = re.compile(
    r"^(?:这|那|它|其|该|上述|前者|后者|此)(?:个|些|种|项|一点|一步|部分)?"
)


def _normalise_whitespace(value: str) -> str:
    """Normalize wrapping without turning ``AI Agent`` into ``AIAgent``."""

    def replacement(match: re.Match[str]) -> str:
        before = value[match.start() - 1] if match.start() else ""
        after = value[match.end()] if match.end() < len(value) else ""
        return (
            " "
            if before.isascii()
            and before.isalnum()
            and after.isascii()
            and after.isalnum()
            else ""
        )

    return re.sub(r"\s+", replacement, value).strip()


def _claim_hash(claim: str) -> str:
    return hashlib.sha256(_normalise_whitespace(claim).encode("utf-8")).hexdigest()


def _word_separator(left: str, right: str) -> str:
    """Keep a word boundary for wrapped ASCII prose, not wrapped CJK prose."""

    if (
        left
        and right
        and re.match(r"[A-Za-z0-9]$", left)
        and re.match(r"^[A-Za-z0-9]", right)
    ):
        return " "
    return ""


def _frontmatter(card: Any) -> Mapping[str, Any]:
    value = getattr(card, "frontmatter", None)
    return value if isinstance(value, Mapping) else {}


def _card_value(card: Any, name: str, default: Any = "") -> Any:
    if isinstance(card, Mapping) and name in card:
        return card[name]
    return getattr(card, name, default)


@dataclass(frozen=True)
class _EvidenceSegment:
    """One canonical source container, mapped to physical card lines."""

    lines: tuple[tuple[int, str], ...]


def _markdown_lines(card: Any) -> list[str]:
    value = _card_value(card, "markdown", "")
    if not isinstance(value, str) or not value:
        raise CandidateError("missing_card_markdown")
    return value.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _clean_html(line: str) -> str:
    return re.sub(r"<[^>]+>", "", line).strip()


def _is_approved_summary(line: str) -> bool:
    """Match SourceMaster's canonical, attribute-free raw summary tag exactly."""

    match = re.fullmatch(r"\s*<summary>(.*?)</summary>\s*", line, re.I)
    return bool(match and _APPROVED_SUMMARY_TEXT.fullmatch(match.group(1)))


def _first_details_summary_index(container: list[tuple[int, str]]) -> int | None:
    """Return the first structural item only when it is an approved summary."""

    for position, (_, raw) in enumerate(container):
        if position == 0:
            opening = re.match(r"^\s*<details>\s*(.*)$", raw, re.I)
            if not opening:
                return None
            raw = opening.group(1)
        if not raw.strip():
            continue
        return position if _is_approved_summary(raw) else None
    return None


def _without_generated(lines: list[str]) -> list[tuple[int, str]]:
    """Keep physical lines while removing the entire generated-relations range."""

    result: list[tuple[int, str]] = []
    in_generated = False
    for line_number, line in enumerate(lines, start=1):
        if _GENERATED_BEGIN in line:
            in_generated = True
            continue
        if _GENERATED_END in line:
            in_generated = False
            continue
        if not in_generated:
            result.append((line_number, line))
    return result


def _segment_from_lines(lines: Iterable[tuple[int, str]]) -> _EvidenceSegment | None:
    cleaned = tuple(
        (line_number, _clean_html(line))
        for line_number, line in lines
        if _clean_html(line)
    )
    return _EvidenceSegment(cleaned) if cleaned else None


def _has_malformed_transcript_containers(physical_lines: list[str]) -> bool:
    """Fail closed on nested, crossing, or unclosed canonical HTML containers."""

    stack: list[str] = []
    tokens = re.compile(r"<details\b|</details\s*>|</pre\s*>", re.I)
    for _, line in _without_generated(physical_lines):
        events: list[tuple[int, str]] = [
            (match.start(), match.group().lower()) for match in tokens.finditer(line)
        ]
        events.extend((match.start(), "raw-pre") for match in _RAW_PRE.finditer(line))
        for _, event in sorted(events):
            if event.startswith("<details") and not event.startswith("</"):
                if stack:
                    return True
                stack.append("details")
            elif event.startswith("</details"):
                if not stack or stack[-1] != "details":
                    return True
                stack.pop()
            elif event == "raw-pre":
                if stack not in ([], ["details"]):
                    return True
                stack.append("pre")
            elif event.startswith("</pre"):
                if not stack or stack[-1] != "pre":
                    return True
                stack.pop()
    return bool(stack)


def _transcript_segments(card: Any, *, raw_only: bool = False) -> tuple[_EvidenceSegment, ...]:
    """Whitelist canonical transcript containers, never ordinary card prose."""

    physical_lines = _markdown_lines(card)
    if _has_malformed_transcript_containers(physical_lines):
        return ()
    lines = _without_generated(physical_lines)
    segments: list[_EvidenceSegment] = []
    detail_line_numbers: set[int] = set()
    position = 0
    while position < len(lines):
        _, line = lines[position]
        if not re.search(r"<details\b", line, re.I):
            position += 1
            continue
        end = position + 1
        nested = False
        while end < len(lines) and not re.search(r"</details\s*>", lines[end][1], re.I):
            if re.search(r"<details\b", lines[end][1], re.I):
                nested = True
            end += 1
        if end >= len(lines):
            # Canonical details evidence must have its own closing tag; do not
            # turn trailing/derived prose into a permissive open-ended segment.
            detail_line_numbers.update(
                line_number for line_number, _ in lines[position:]
            )
            break
        container = lines[position : end + 1]
        detail_line_numbers.update(line_number for line_number, _ in container)
        summary_index = None if nested else _first_details_summary_index(container)
        if summary_index is not None and not raw_only:
            body: list[tuple[int, str]] = []
            for line_number, raw in container[summary_index + 1 :]:
                if not re.search(r"</?details\b", raw, re.I):
                    body.append((line_number, raw))
            segment = _segment_from_lines(body)
            if segment:
                segments.append(segment)
        position = end + 1

    # SourceMaster explicitly recognizes a standalone approved summary through
    # the generated-relations boundary as a complete original-text container.
    # Require that boundary here so a stray summary cannot authorize arbitrary
    # following card prose.
    for line_number, line in enumerate(physical_lines, start=1):
        if raw_only or line_number in detail_line_numbers or not _is_approved_summary(line):
            continue
        begin_index = next(
            (
                index
                for index in range(line_number, len(physical_lines))
                if _GENERATED_BEGIN in physical_lines[index]
            ),
            None,
        )
        if begin_index is None:
            continue
        body = [
            (index + 1, physical_lines[index])
            for index in range(line_number, begin_index)
        ]
        segment = _segment_from_lines(body)
        if segment:
            segments.append(segment)

    # A raw-transcript pre tag is itself a recognized lv-know container.  It
    # is captured even when older cards do not wrap it in a details element.
    position = 0
    while position < len(lines):
        _, line = lines[position]
        if not _RAW_PRE.search(line):
            position += 1
            continue
        body: list[tuple[int, str]] = []
        closed = False
        nested = False
        current = line.split(">", 1)[1] if ">" in line else ""
        if "</pre>" in current.lower():
            body.append(
                (lines[position][0], re.split(r"</pre\s*>", current, flags=re.I)[0])
            )
            closed = True
        else:
            if current:
                body.append((lines[position][0], current))
            position += 1
            while position < len(lines) and "</pre>" not in lines[position][1].lower():
                if _RAW_PRE.search(lines[position][1]):
                    nested = True
                body.append(lines[position])
                position += 1
            if position < len(lines):
                body.append(
                    (
                        lines[position][0],
                        re.split(r"</pre\s*>", lines[position][1], flags=re.I)[0],
                    )
                )
                closed = True
        segment = _segment_from_lines(body) if closed and not nested else None
        if segment:
            segments.append(segment)
        if not closed:
            # No later line may be misread as a second independent pre after a
            # malformed open container.
            break
        position += 1
    return tuple(segments)


def _research_segments(card: Any) -> tuple[_EvidenceSegment, ...]:
    """Use only the explicit report-template `可引用原文` quote container.

    The current report contract does not expose a raw HTML chapter-body marker
    in the card.  Its explicit, direct-quote section is therefore the narrow
    machine-readable entry point; findings and derived judgments are excluded.
    """

    lines = _without_generated(_markdown_lines(card))
    segments: list[_EvidenceSegment] = []
    active_level: int | None = None
    quote_block: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal quote_block
        segment = _segment_from_lines(
            (line_number, re.sub(r"^\s*>\s?", "", value))
            for line_number, value in quote_block
        )
        if segment:
            segments.append(segment)
        quote_block = []

    for line_number, line in lines:
        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if active_level is not None and level <= active_level:
                flush()
                active_level = None
            if title == "可引用原文":
                active_level = level
            continue
        if active_level is None:
            continue
        if re.match(r"^\s*>", line):
            quote_block.append((line_number, line))
        elif quote_block:
            flush()
    if quote_block:
        flush()
    return tuple(segments)


def _bundle_segments(card: Any) -> tuple[_EvidenceSegment, ...]:
    """Accept direct quotes from the bundle's exact source-extract path."""

    lines = _without_generated(_markdown_lines(card))
    segments: list[_EvidenceSegment] = []
    in_source_section = False
    in_quote_section = False
    quote_block: list[tuple[int, str]] = []
    details_depth = 0
    malformed_details = False
    in_html_comment = False
    fence: tuple[str, int, bool] | None = None
    malformed_fence = False

    def flush() -> None:
        nonlocal quote_block
        segment = _segment_from_lines(quote_block)
        if segment:
            segments.append(segment)
        quote_block = []

    for line_number, line in lines:
        quote = re.match(r"^ {0,3}>[ \t]?(.*)$", line)
        quote_payload = quote.group(1) if quote else None

        if malformed_fence:
            continue

        if malformed_details:
            continue

        if fence is not None:
            marker, minimum_length, opened_in_quote = fence
            current_in_quote = quote_payload is not None
            if current_in_quote != opened_in_quote:
                flush()
                fence = None
                malformed_fence = True
                continue
            fence_line = quote_payload if opened_in_quote else line
            if re.fullmatch(
                rf" {{0,3}}{re.escape(marker)}{{{minimum_length},}}[ \t]*",
                fence_line,
            ):
                fence = None
            continue

        if in_html_comment:
            if "-->" in line:
                in_html_comment = False
            continue

        structural_line = quote_payload if quote_payload is not None else line
        if "<!--" in structural_line:
            flush()
            if "-->" not in structural_line.split("<!--", 1)[1]:
                in_html_comment = True
            continue

        fence_open = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", structural_line)
        if fence_open and not (
            fence_open.group(1).startswith("`") and "`" in fence_open.group(2)
        ):
            flush()
            marker_text = fence_open.group(1)
            fence = (marker_text[0], len(marker_text), quote is not None)
            continue

        if re.search(r"</?details\b", structural_line, re.I):
            flush()
            tokens = list(
                re.finditer(
                    r"<details\b[^>]*>|</details\s*>",
                    structural_line,
                    re.I,
                )
            )
            if not tokens:
                malformed_details = True
                continue
            for token in tokens:
                if token.group().lower().startswith("</"):
                    if details_depth == 0:
                        malformed_details = True
                    else:
                        details_depth -= 1
                else:
                    details_depth += 1
            continue

        if re.match(r"^(?: {4,}|\t)", line):
            flush()
            continue

        heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$", line)
        if heading:
            flush()
            if details_depth:
                continue
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if level <= 2:
                in_source_section = level == 2 and title == "原素材提炼"
                in_quote_section = False
            elif level == 3:
                in_quote_section = in_source_section and title == "原话摘录"
            else:
                in_quote_section = False
            continue

        if details_depth or malformed_details or not in_quote_section:
            continue

        if quote_payload is not None:
            if re.match(r"^(?: {4,}|\t| {0,3}>)", quote_payload):
                flush()
                continue
            quote_block.append((line_number, quote_payload))
        elif quote_block:
            flush()
    if quote_block:
        flush()
    return tuple(segments)


def _evidence_segments(card: Any) -> tuple[_EvidenceSegment, ...]:
    source_type = str(_frontmatter(card).get("source_type", ""))
    if source_type == "research_report":
        return _research_segments(card)
    if source_type == "short_video_bundle":
        # A bundle card represents one video cut from a multi-video source
        # packet.  Its direct-quote blocks remain the preferred narrow
        # evidence, while an approved canonical transcript container is also
        # original source material for that same card.  Ordinary prose and
        # unapproved details remain excluded by both parsers.
        return _bundle_segments(card) + _transcript_segments(card)
    if source_type in {"short_video", "long_form", "unknown", "article_or_interview"}:
        return _transcript_segments(card)
    return ()


def _segment_text(segment: _EvidenceSegment) -> str:
    return "\n".join(raw.strip() for _, raw in segment.lines if raw.strip())


def model_evidence_segments(card: Any) -> tuple[str, ...]:
    """Return one deduplicated, source-only representation for model input.

    The validator may accept both narrow direct quotes and a canonical full
    transcript.  Sending both would overweight repeated quotes, so bundle cards
    prefer the canonical transcript when present and fall back to the exact
    quote path only when no transcript exists.  Generated and ordinary card
    prose never enter this payload.
    """

    source_type = str(_frontmatter(card).get("source_type", ""))
    raw_segments = _transcript_segments(card, raw_only=True) if source_type in {
        "short_video", "short_video_bundle", "long_form", "unknown", "article_or_interview"
    } else ()
    if raw_segments:
        segments = raw_segments
    elif source_type == "short_video_bundle":
        segments = _transcript_segments(card)
        if not segments:
            segments = _bundle_segments(card)
    else:
        segments = _evidence_segments(card)
    result: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        text = _segment_text(segment)
        fingerprint = _normalise_whitespace(text)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(text)
    return tuple(result)


def _matches_in_segment(
    segment: _EvidenceSegment, claim: str
) -> tuple[EvidenceMatch, ...]:
    wanted = _normalise_whitespace(claim)
    text = ""
    ranges: list[tuple[int, int, int]] = []
    for line_number, raw in segment.lines:
        part = _normalise_whitespace(raw)
        if not part:
            continue
        separator = _word_separator(text, part)
        text += separator
        start = len(text)
        text += part
        ranges.append((start, len(text), line_number))
    if not wanted or not text:
        return ()
    matches: list[EvidenceMatch] = []
    start_at = 0
    while True:
        found = text.find(wanted, start_at)
        if found < 0:
            break
        end = found + len(wanted)
        overlapping = [
            line for left, right, line in ranges if right > found and left < end
        ]
        if overlapping:
            matches.append(
                EvidenceMatch(overlapping[0], overlapping[-1], _claim_hash(claim))
            )
        start_at = found + 1
    return tuple(matches)


def _evidence_matches(card: Any, claim: str) -> tuple[EvidenceMatch, ...]:
    matches: list[EvidenceMatch] = []
    for segment in _evidence_segments(card):
        matches.extend(_matches_in_segment(segment, claim))
    return tuple(matches)


def locate_source_claim(card: Any, claim: str) -> EvidenceMatch | None:
    """Find the first permitted original-text occurrence of ``claim``.

    The returned locator includes physical 1-based Markdown card lines and a
    normalized-claim SHA-256.  It only searches one canonical evidence segment
    at a time, so adjacent source containers cannot be stitched into evidence.
    """

    matches = _evidence_matches(card, claim)
    return matches[0] if matches else None


def fragment_reasons(claim: str) -> tuple[str, ...]:
    """Explain only high-confidence incomplete-claim patterns.

    Numbering alone is intentionally not a failure: ``第一，企业应…`` is a
    complete proposition.  The rule targets dependent conversational fragments
    such as ``第二呢，其实就是…`` and leading unresolved references.
    """

    text = _normalise_whitespace(claim)
    reasons: list[str] = []
    if len(text) < 8:
        reasons.append("too_short")
    if _ORDINAL_FRAGMENT.search(text):
        reasons.append("unresolved_step_prefix")
    if _LEADING_PRONOUN.search(text):
        reasons.append("unresolved_pronoun")
    if text.endswith(("，", ",", "、", "和", "与", "及", "等", "的")):
        reasons.append("unfinished_ending")
    return tuple(reasons)


def _required_missing(candidate: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for name in REQUIRED:
        if name not in candidate or candidate[name] is None:
            missing.append(name)
            continue
        if name == "disposition_reason":
            continue
        if isinstance(candidate[name], str) and not candidate[name].strip():
            missing.append(name)
    return missing


def _require_card_provenance(candidate: Mapping[str, Any], card: Any) -> None:
    card_id = str(_card_value(card, "id", ""))
    if str(candidate["source_card_id"]) != card_id:
        raise CandidateError("source_card_id_mismatch")
    frontmatter = _frontmatter(card)
    if str(candidate["source_unit_id"]) != str(frontmatter.get("source_unit_id", "")):
        raise CandidateError("source_unit_id_mismatch")
    expected_blogger = str(
        frontmatter.get("author_id") or frontmatter.get("blogger") or ""
    )
    if str(candidate["source_blogger"]) != expected_blogger:
        raise CandidateError("source_blogger_mismatch")
    if str(candidate["source_type"]) != str(frontmatter.get("source_type", "")):
        raise CandidateError("source_type_mismatch")
    if str(candidate["primary_theme"]) != str(frontmatter.get("primary_theme", "")):
        raise CandidateError("primary_theme_mismatch")


def validate_candidate(
    candidate: Mapping[str, Any], card: Any, expected_semantic_input_sha256: str
) -> None:
    """Validate one model candidate against the matching current SourceMaster.

    Every disposition remains auditable.  Only candidates marked ``进入三层``
    pass the full claim, content-hash, semantic-hash, and original-evidence
    quality gate; review/layer-2-only rows remain recorded with a reason.  The
    expected semantic hash is explicit immutable manifest context, rather than
    a mutable attribute on a frozen SourceMaster.
    """

    if not isinstance(candidate, Mapping):
        raise CandidateError("candidate_must_be_object")
    missing = _required_missing(candidate)
    if missing:
        raise CandidateError("missing_required_fields", ",".join(missing))
    disposition = str(candidate["disposition"])
    if disposition not in ALLOWED_DISPOSITIONS:
        raise CandidateError("invalid_disposition", disposition)
    if (
        disposition in {"待人工判断", "仅保留二层"}
        and not str(candidate["disposition_reason"]).strip()
    ):
        raise CandidateError("missing_disposition_reason")

    _require_card_provenance(candidate, card)
    if disposition != "进入三层":
        return

    if fragment_reasons(str(candidate["claim"])):
        raise CandidateError("fragmentary_claim")
    if (
        str(candidate["source_card_sha256"]).lower()
        != str(_card_value(card, "card_content_sha256", "")).lower()
    ):
        raise CandidateError("source_card_hash_mismatch")
    expected = str(expected_semantic_input_sha256).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise CandidateError("invalid_expected_semantic_hash")
    if str(candidate["semantic_input_sha256"]).lower() != expected:
        raise CandidateError("semantic_hash_mismatch")

    matches = _evidence_matches(card, str(candidate["source_claim"]))
    locator = str(candidate["source_evidence_locator"])
    if not matches or locator not in {match.locator for match in matches}:
        raise CandidateError("evidence_not_found")


def _scope_cards(scope: Any) -> list[Mapping[str, Any]]:
    rows = scope.get("cards") if isinstance(scope, Mapping) else scope
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes, Mapping)):
        raise CandidateError("scope_requires_cards")
    cards = list(rows)
    for position, row in enumerate(cards, start=1):
        if not isinstance(row, Mapping) or not str(row.get("id", "")).strip():
            raise CandidateError("invalid_scope_card", str(position))
    return cards


def audit_card_dispositions(
    scope: Any, candidates: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Require one disposition row per scoped mother card and return its audit."""

    cards = _scope_cards(scope)
    scoped_ids = [str(card["id"]) for card in cards]
    if len(set(scoped_ids)) != len(scoped_ids):
        raise CandidateError("duplicate_scope_card_id")
    records: dict[str, list[dict[str, str]]] = {card_id: [] for card_id in scoped_ids}
    for position, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, Mapping):
            raise CandidateError("candidate_must_be_object", str(position))
        source_id = str(candidate.get("source_card_id", "")).strip()
        if not source_id:
            raise CandidateError("missing_source_card_id", str(position))
        if source_id not in records:
            raise CandidateError("unknown_candidate_source_id", source_id)
        disposition = str(candidate.get("disposition", "")).strip()
        if disposition not in ALLOWED_DISPOSITIONS:
            raise CandidateError("invalid_disposition", disposition or source_id)
        reason = str(candidate.get("disposition_reason", "")).strip()
        if disposition in {"待人工判断", "仅保留二层"} and not reason:
            raise CandidateError("missing_disposition_reason", source_id)
        records[source_id].append(
            {
                "candidate_id": str(candidate.get("candidate_id", "")),
                "disposition": disposition,
                "disposition_reason": reason,
            }
        )
    missing = [card_id for card_id, rows in records.items() if not rows]
    if missing:
        raise CandidateError("cards_without_disposition", ",".join(missing))
    return {"cards": records, "scope_card_count": len(cards)}
