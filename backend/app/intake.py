from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path

from . import llm
from .models import (
    IntakeConfirmRequest,
    ParsedSeminar,
    QuestionDefinition,
    RelevanceReport,
    ResearchSource,
    SeminarPreset,
)
from .research import AcademicSearcher, _tokens

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_ROOT.parent / "data"
PROFILE_PATH = BACKEND_ROOT / "config" / "research_profile.md"

PROFILE_TEMPLATE = """# My research profile

Describe your research directions here, one bullet per line. The intake
pipeline reads this file to judge how a seminar announcement relates to
your work and to propose questions worth asking.

- direction one
- direction two
"""

PRESET_ID_SAFE = re.compile(r"[^a-z0-9]+")
PRESET_ID_CHECK = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), False),
    (re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})"), False),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), True),
]

SPEAKER_LABELS = (
    "speaker",
    "演讲人",
    "报告人",
    "主讲人",
    "报告嘉宾",
    "主讲嘉宾",
)


async def _run_llm(prompt: str, timeout: float) -> dict:
    return await llm.complete_json(prompt, timeout, cwd=str(BACKEND_ROOT))


def _clean_list(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


GLOSSARY_BOILERPLATE = re.compile(
    r"讲座预告|讲座通知|学术研讨会|研讨会通知|预告|报名|扫码|海报|二维码|直播|"
    r"回放|会议通知|时间地点|主办单位|承办单位|欢迎参加|讲座系列|图片|详情|"
    r"registration|register|poster|qr ?code|live.?stream",
    re.IGNORECASE,
)
_CHINESE_RUN = re.compile(r"^[\u4e00-\u9fff]+$")


def _clean_glossary(items: list[str], limit: int = 30) -> list[str]:
    """Keep only real domain terms; drop notice boilerplate and title fragments."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        term = str(item).strip()
        if not term or term.lower() in seen:
            continue
        if GLOSSARY_BOILERPLATE.search(term):
            continue
        # 纯中文且超过 12 字的几乎都是标题/正文碎片，不是术语
        if _CHINESE_RUN.fullmatch(term) and len(term) > 12:
            continue
        seen.add(term.lower())
        cleaned.append(term)
    return cleaned[:limit]


async def parse_announcement_deepseek(raw_text: str) -> ParsedSeminar:
    prompt = (
        "You parse academic seminar announcements. Do not use tools and do not "
        "invent facts: leave a field empty when the announcement does not state it. "
        "Return exactly one compact JSON object without markdown with schema: "
        '{"title":"...","speaker":"...","speaker_affiliation":"...","date":"YYYY-MM-DD",'
        '"abstract":"...","topic_keywords":["..."]}. '
        "Keep the abstract under 2000 characters and use 3-8 short technical or "
        "domain terms as topic keywords (methods, phenomena, places, datasets). "
        "Never use event boilerplate (讲座预告/报名/海报/seminar notice) or copy "
        "title fragments verbatim.\n\n"
        f"ANNOUNCEMENT:\n{raw_text}"
    )
    payload = await _run_llm(prompt, 90)
    return ParsedSeminar(
        title=str(payload.get("title") or "").strip()[:300],
        speaker=str(payload.get("speaker") or "").strip()[:200],
        speaker_affiliation=str(payload.get("speaker_affiliation") or "").strip()[:300],
        date=str(payload.get("date") or "").strip()[:40],
        abstract=str(payload.get("abstract") or "").strip()[:2000],
        topic_keywords=_clean_list(payload.get("topic_keywords"), 8),
        parse_method="deepseek",
    )


def parse_announcement_heuristic(raw_text: str) -> ParsedSeminar:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    title = ""
    speaker = ""
    affiliation = ""
    found_date = ""

    for pattern, month_first in DATE_PATTERNS:
        match = pattern.search(raw_text)
        if match:
            groups = match.groups()
            if month_first:
                month, day, year = groups
            else:
                year, month, day = groups
            try:
                parsed = date_type(int(year), int(month), int(day))
                found_date = parsed.isoformat()
            except ValueError:
                continue
            break

    for index, line in enumerate(lines[:30]):
        lower = line.lower()
        is_label = any(label in lower for label in SPEAKER_LABELS)
        if is_label:
            text = re.sub(r"[：:]", " ", line, count=1)
            remainder = text.strip()
            for label in SPEAKER_LABELS:
                remainder = re.sub(
                    rf"(?i){re.escape(label)}\s*", "", remainder, count=1
                ).strip()
            remainder = remainder.strip("-–— \t")
            if remainder and not speaker:
                parts = re.split(r"[,，;；(/(]| - | – | — ", remainder, maxsplit=1)
                speaker = parts[0].strip()[:200]
                if len(parts) > 1:
                    affiliation = parts[1].strip(" ，,；;）)")[:300]

    if not title and lines:
        for line in lines[:5]:
            if line.lower() == "title" or "seminar" in line.lower() or "讲座" in line:
                continue
            if re.search(r"[：:]", line) and not speaker:
                continue
            if len(line) >= 8 and not re.match(r"^\d", line):
                title = line.strip(" #*\t")[:300]
                break
        if not title:
            title = lines[0].strip(" #*\t")[:300]

    return ParsedSeminar(
        title=title,
        speaker=speaker,
        speaker_affiliation=affiliation,
        date=found_date,
        abstract=raw_text[:2000],
        topic_keywords=sorted(_tokens(" ".join([title, *lines[:5]])))[:8],
        parse_method="heuristic",
    )


async def parse_announcement(raw_text: str) -> ParsedSeminar:
    if llm.llm_available():
        try:
            parsed = await parse_announcement_deepseek(raw_text)
            if parsed.title or parsed.speaker:
                return parsed
        except Exception:
            pass
    return parse_announcement_heuristic(raw_text)


def load_research_profile() -> str:
    if PROFILE_PATH.exists():
        return PROFILE_PATH.read_text(encoding="utf-8")
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(PROFILE_TEMPLATE, encoding="utf-8")
    return PROFILE_TEMPLATE


def _profile_has_content(profile: str) -> bool:
    bullets = [
        line.lstrip("-* ").strip()
        for line in profile.splitlines()
        if line.lstrip().startswith(("-", "*"))
    ]
    filled = [item for item in bullets if item and not item.startswith("direction ")]
    return bool(filled)


async def assess_relevance_deepseek(
    parsed: ParsedSeminar, profile: str
) -> RelevanceReport:
    prompt = (
        "You compare an academic seminar announcement against a researcher's own "
        "profile. Do not use tools. Judge only stated overlaps; do not invent them. "
        "Return exactly one compact JSON object without markdown with schema: "
        '{"score":1,"summary":"...","overlap_directions":["..."]}. '
        "score is an integer 1-5 (1 = unrelated, 5 = core to the researcher's work), "
        "summary is one Chinese sentence, and overlap_directions lists up to four "
        "short Chinese phrases naming the concrete shared directions.\n\n"
        f"RESEARCHER PROFILE:\n{profile[:6000]}\n\n"
        f"SEMINAR:\n{json.dumps(parsed.model_dump(), ensure_ascii=False)}"
    )
    payload = await _run_llm(prompt, 75)
    try:
        score = max(1, min(5, int(payload.get("score") or 1)))
    except (TypeError, ValueError):
        score = 1
    return RelevanceReport(
        score=score,
        summary=str(payload.get("summary") or "").strip()[:500],
        overlap_directions=_clean_list(payload.get("overlap_directions"), 4),
        method="deepseek",
    )


def assess_relevance_keyword(parsed: ParsedSeminar, profile: str) -> RelevanceReport:
    announcement_tokens = _tokens(
        " ".join([parsed.title, parsed.abstract, *parsed.topic_keywords])
    )
    profile_tokens = _tokens(profile)
    overlap = sorted(announcement_tokens & profile_tokens)
    directions = [item for item in overlap[:4]]
    if not announcement_tokens or not profile_tokens:
        score = 1
    else:
        ratio = len(overlap) / max(1, min(len(announcement_tokens), 20))
        score = max(1, min(5, 1 + round(ratio * 8)))
    summary = (
        f"关键词重叠：{'、'.join(directions)}" if directions else "未发现明显关键词重叠"
    )
    return RelevanceReport(
        score=score, summary=summary, overlap_directions=directions, method="keyword"
    )


async def assess_relevance(parsed: ParsedSeminar) -> RelevanceReport:
    profile = load_research_profile()
    if not _profile_has_content(profile):
        return RelevanceReport(
            score=0,
            summary="尚未填写研究方向（backend/config/research_profile.md），无法判断相关性",
            overlap_directions=[],
            method="keyword",
        )
    if llm.llm_available():
        try:
            return await assess_relevance_deepseek(parsed, profile)
        except Exception:
            pass
    return assess_relevance_keyword(parsed, profile)


def search_speaker_works(speaker: str, affiliation: str) -> tuple[list[ResearchSource], list[str]]:
    notes: list[str] = []
    sources: list[ResearchSource] = []
    if not speaker.strip():
        return sources, notes
    searcher = AcademicSearcher()
    query = speaker.strip()
    if affiliation.strip():
        query = f"{speaker.strip()} {affiliation.strip()}"
    outcome = searcher.search(query, limit=4)
    for source in outcome.sources:
        if source.source_type == "openalex":
            source = source.model_copy(update={"source_type": "openalex_author"})
        sources.append(source)
    notes.extend(outcome.notes)
    return sources[:4], notes


def search_topic_works(parsed: ParsedSeminar) -> tuple[list[ResearchSource], list[str]]:
    query = " ".join([parsed.title, *parsed.topic_keywords]).strip()
    if not query:
        return [], []
    outcome = AcademicSearcher().search(query, limit=6)
    return outcome.sources, outcome.notes


async def propose_questions_deepseek(
    parsed: ParsedSeminar,
    relevance: RelevanceReport,
    sources: list[ResearchSource],
) -> list[QuestionDefinition]:
    source_lines = [
        f"- {source.title} ({source.year or 'n.d.'})" for source in sources[:6]
    ]
    prompt = (
        "You prepare questions for a researcher to ask at an academic seminar. "
        "Do not use tools and do not invent claims beyond the announcement. "
        "Write each question in Chinese, specific enough to be asked aloud, and "
        "prioritise angles that matter given the researcher's own directions when "
        "they overlap the seminar. Return exactly one compact JSON object without "
        'markdown with schema: {"questions":[{"question":"...","why_it_matters":"...",'
        '"keywords":["..."],"expected_slots":["..."]}]}. '
        "Provide 4-6 questions, 3-6 keywords each, and 2-4 expected answer slots "
        "each.\n\n"
        f"SEMINAR:\n{json.dumps(parsed.model_dump(), ensure_ascii=False)}\n\n"
        f"RELEVANCE TO RESEARCHER (1-5): {relevance.score}\n"
        f"OVERLAP DIRECTIONS: {json.dumps(relevance.overlap_directions, ensure_ascii=False)}\n\n"
        f"CANDIDATE REFERENCES:\n{chr(10).join(source_lines) or '(none)'}"
    )
    payload = await _run_llm(prompt, 90)
    questions: list[QuestionDefinition] = []
    for index, item in enumerate(payload.get("questions", [])):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        questions.append(
            QuestionDefinition(
                id=f"q{index + 1}",
                question=question[:500],
                why_it_matters=str(item.get("why_it_matters") or "").strip()[:500],
                keywords=_clean_list(item.get("keywords"), 6),
                expected_slots=_clean_list(item.get("expected_slots"), 4),
            )
        )
    return questions


async def propose_questions(
    parsed: ParsedSeminar,
    relevance: RelevanceReport,
    sources: list[ResearchSource],
) -> list[QuestionDefinition]:
    if not llm.llm_available() or not parsed.title:
        return []
    try:
        return await propose_questions_deepseek(parsed, relevance, sources)
    except Exception:
        return []


def _slugify(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_part = "".join(ch for ch in decomposed if ch.isascii() and ch.isalnum())
    slug = PRESET_ID_SAFE.sub("-", ascii_part.lower()).strip("-")
    return slug


def build_preset_id(speaker: str, title: str, seminar_date: str, existing: set[str]) -> str:
    name = _slugify(speaker.split()[0] if speaker.split() else "")
    stem = _slugify(title.split(":")[0])[:24]
    date_part = ""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", seminar_date.strip())
    if match:
        date_part = match.group(0)
    elif not name and not stem:
        date_part = date_type.today().isoformat()
    base = "-".join(part for part in (name, stem, date_part) if part) or "seminar"
    candidate = base[:80]
    suffix = 2
    while candidate in existing:
        candidate = f"{base[:76]}-{suffix}"
        suffix += 1
    return candidate


def validate_preset_id(preset_id: str) -> bool:
    return bool(PRESET_ID_CHECK.match(preset_id)) and len(preset_id) <= 100


def write_intake_preset(
    request: IntakeConfirmRequest, preset_id: str, config_dir: Path
) -> Path:
    preset = SeminarPreset(
        id=preset_id,
        title=request.parsed.title.strip() or "未命名讲座",
        speaker=request.parsed.speaker.strip() or "未知演讲者",
        date=request.parsed.date.strip() or date_type.today().isoformat(),
        source_path=None,
        glossary=_clean_glossary(request.glossary),
        questions=request.questions,
    )
    target = config_dir / f"{preset_id}.json"
    target.write_text(
        json.dumps(preset.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def write_dossier(request: IntakeConfirmRequest, preset_id: str, data_dir: Path) -> Path:
    dossier = {
        "preset_id": preset_id,
        "created_at": date_type.today().isoformat(),
        "raw_text": request.raw_text,
        "parsed": request.parsed.model_dump(),
        "relevance": request.relevance.model_dump(),
        "research_sources": [source.model_dump() for source in request.research_sources],
        "research_notes": request.research_notes,
    }
    target = data_dir / "dossiers" / preset_id / "dossier.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dossier, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target
