from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import ResearchSource, TranscriptSegment


USER_AGENT = "SeminarCopilot/0.1 (local academic question assistant)"


@dataclass(frozen=True)
class SearchOutcome:
    sources: list[ResearchSource]
    notes: list[str]


def _normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _tokens(value: str) -> set[str]:
    generic = {
        "analysis",
        "data",
        "effect",
        "effects",
        "health",
        "method",
        "model",
        "research",
        "study",
        "using",
    }
    return {
        item
        for item in re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", value.lower())
        if item
        not in {
            "what",
            "which",
            "how",
            "why",
            "是否",
            "如何",
            "什么",
            *generic,
        }
    }


def bibliographic_relevance(query: str, title: str) -> float:
    query_tokens = _tokens(query)
    title_tokens = _tokens(title)
    if not query_tokens or not title_tokens:
        return 0.0
    overlap = query_tokens & title_tokens
    return len(overlap) / max(2.0, len(title_tokens) ** 0.5)


def search_transcript(
    query: str,
    keywords: list[str],
    segments: list[tuple[TranscriptSegment, float]],
    limit: int = 4,
) -> list[ResearchSource]:
    needles = _tokens(" ".join([query, *keywords]))
    ranked: list[tuple[float, TranscriptSegment, float]] = []
    for segment, offset in segments:
        text_lower = segment.text.lower()
        overlap = needles & _tokens(segment.text)
        phrase_hits = sum(
            1 for item in keywords if len(item.strip()) >= 3 and item.lower() in text_lower
        )
        score = phrase_hits * 2.0 + len(overlap)
        if score > 0:
            ranked.append((score, segment, offset))
    ranked.sort(key=lambda item: (item[0], item[1].end), reverse=True)
    return [
        ResearchSource(
            source_type="transcript",
            title=f"讲座转录 {format_seconds(offset + segment.start)}",
            snippet=segment.text,
            confidence=min(0.95, 0.45 + score * 0.08),
        )
        for score, segment, offset in ranked[:limit]
    ]


class AcademicSearcher:
    def __init__(self) -> None:
        self.openalex_key = os.environ.get("OPENALEX_API_KEY") or os.environ.get(
            "OPENALEX_KEY"
        )

    def search(self, query: str, limit: int = 6) -> SearchOutcome:
        sources: list[ResearchSource] = []
        notes: list[str] = []
        for name, loader in (("OpenAlex", self._openalex), ("Crossref", self._crossref)):
            try:
                sources.extend(loader(query))
            except Exception as exc:
                notes.append(f"{name} unavailable: {type(exc).__name__}")
        ranked: list[tuple[float, ResearchSource]] = []
        seen: set[str] = set()
        for source in sources:
            key = _normalized_title(source.title)
            if not key or key in seen:
                continue
            seen.add(key)
            relevance = bibliographic_relevance(query, source.title)
            if relevance < 0.7:
                continue
            ranked.append(
                (
                    relevance,
                    source.model_copy(
                        update={
                            "confidence": min(
                                0.9, source.confidence + min(0.16, relevance * 0.08)
                            )
                        }
                    ),
                )
            )
        ranked.sort(key=lambda item: item[0], reverse=True)
        if sources and not ranked:
            notes.append("No sufficiently relevant bibliographic matches")
        return SearchOutcome([source for _, source in ranked[:limit]], notes)

    def _get_json(self, base: str, params: dict[str, str]) -> dict[str, Any]:
        request = Request(f"{base}?{urlencode(params)}", headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=12) as response:  # noqa: S310 - fixed HTTPS endpoints
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Unexpected search response")
        return payload

    def _openalex(self, query: str) -> list[ResearchSource]:
        params = {"search": query, "per-page": "4"}
        if self.openalex_key:
            params["api_key"] = self.openalex_key
        payload = self._get_json("https://api.openalex.org/works", params)
        results: list[ResearchSource] = []
        for item in payload.get("results", []):
            title = str(item.get("display_name") or "").strip()
            if not title:
                continue
            authors = [
                str(entry.get("author", {}).get("display_name"))
                for entry in item.get("authorships", [])[:5]
                if entry.get("author", {}).get("display_name")
            ]
            doi = str(item.get("doi") or "").strip()
            results.append(
                ResearchSource(
                    source_type="openalex",
                    title=title,
                    url=doi or str(item.get("id") or "") or None,
                    authors=authors,
                    year=item.get("publication_year"),
                    snippet="OpenAlex scholarly-work match",
                    confidence=0.72,
                )
            )
        return results

    def _crossref(self, query: str) -> list[ResearchSource]:
        payload = self._get_json(
            "https://api.crossref.org/works",
            {"query.bibliographic": query, "rows": "4"},
        )
        results: list[ResearchSource] = []
        for item in payload.get("message", {}).get("items", []):
            title_values = item.get("title", [])
            title = str(title_values[0] if title_values else "").strip()
            if not title:
                continue
            authors = [
                " ".join(
                    part
                    for part in (author.get("given", ""), author.get("family", ""))
                    if part
                ).strip()
                for author in item.get("author", [])[:5]
            ]
            date_parts = item.get("published", {}).get("date-parts", [[]])
            year = date_parts[0][0] if date_parts and date_parts[0] else None
            doi = str(item.get("DOI") or "").strip()
            results.append(
                ResearchSource(
                    source_type="crossref",
                    title=title,
                    url=f"https://doi.org/{doi}" if doi else item.get("URL"),
                    authors=[author for author in authors if author],
                    year=year if isinstance(year, int) else None,
                    snippet="Crossref bibliographic match",
                    confidence=0.68,
                )
            )
        return results


def format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"
