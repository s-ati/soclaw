"""
SOCLAW Evidence Evaluation Pipeline

Section-aware retrieval, scoring, ranking, and summarization of
applied execution evidence from candidate profiles.

"Project Evidence" is a report label, not a resume-section dependency.
Evidence may come from work experience, internships, projects, research,
competitions, leadership, or any section showing concrete applied execution.
"""

import re
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# ── Section classification ────────────────────────────────────

SECTION_CATEGORIES: dict[str, list[str]] = {
    "experience": [
        "professional experience", "work experience", "experience",
        "employment", "employment history", "career history",
        "relevant experience", "professional background",
    ],
    "internship": [
        "internship experience", "internships", "internship",
        "praktikum", "werkstudent", "working student",
    ],
    "projects": [
        "projects", "academic projects", "personal projects",
        "key projects", "selected projects", "project experience",
        "capstone", "capstone project",
    ],
    "education": [
        "education", "academic background", "academic history",
        "educational background", "academic qualifications",
    ],
    "research": [
        "research", "research experience", "publications",
        "academic research", "thesis", "dissertation",
    ],
    "leadership": [
        "leadership", "leadership experience", "extracurricular",
        "activities", "extracurricular activities", "student activities",
        "community involvement", "clubs", "organizations",
    ],
    "competitions": [
        "competitions", "awards", "honors", "achievements",
        "case competitions", "hackathons", "honors and awards",
    ],
    "volunteer": [
        "volunteer", "volunteer experience", "community service",
    ],
    "skills": [
        "skills", "technical skills", "core competencies",
        "competencies", "tools", "technologies", "proficiencies",
        "language skills", "languages",
    ],
    "certifications": [
        "certifications", "certificates", "licenses",
        "professional development", "training",
    ],
}

# Sections that can produce evidence (exclude pure list sections)
EVIDENCE_SECTIONS = {
    "experience", "internship", "projects", "research",
    "leadership", "competitions", "volunteer", "education",
}


# ── Scoring vocabularies ─────────────────────────────────────

HIGH_EXEC_VERBS = [
    "built", "created", "developed", "designed", "implemented",
    "conducted", "analyzed", "led", "managed", "coordinated",
    "launched", "deployed", "established", "delivered", "produced",
    "generated", "optimized", "automated", "streamlined", "modeled",
    "forecasted", "compiled", "synthesized", "evaluated", "prepared",
    "constructed", "executed", "resolved", "negotiated", "engineered",
    "formulated", "initiated", "piloted", "configured", "integrated",
    "restructured", "consolidated", "architected", "presented",
    "recommended", "spearheaded",
]

MODERATE_EXEC_VERBS = [
    "assisted", "supported", "contributed", "helped", "participated",
    "collaborated", "researched", "studied", "reviewed", "examined",
    "tested", "monitored", "maintained", "updated", "tracked",
]

OWNERSHIP_SIGNALS = [
    "led", "owned", "responsible for", "end-to-end", "independently",
    "initiated", "spearheaded", "single-handedly", "personally",
    "drove", "championed", "oversaw", "headed", "directed",
    "from scratch", "sole", "my own",
]

DELIVERABLE_KEYWORDS = [
    "model", "dashboard", "report", "analysis", "presentation",
    "workflow", "process", "system", "tool", "framework",
    "strategy", "recommendation", "proposal", "plan", "prototype",
    "pipeline", "database", "template", "script", "automation",
    "visualization", "forecast", "budget", "audit", "assessment",
    "spreadsheet", "tracker", "summary", "brief", "deck",
    "documentation", "specification", "survey", "benchmark",
]

WEAK_SIGNALS = [
    "responsible for", "duties included", "tasked with",
    "familiar with", "exposure to", "knowledge of",
    "understanding of", "awareness of",
]

_YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')
_BULLET_PREFIX_RE = re.compile(r'^[\s•\-\*–·▪►◦■○]+')


# ── Section parsing ───────────────────────────────────────────

def classify_section_header(line: str) -> str | None:
    """Return section category if line looks like a header, else None."""
    stripped = line.strip().rstrip(':').rstrip('–').rstrip('-').strip()
    if len(stripped) > 60 or len(stripped) < 3:
        return None
    if len(stripped.split()) > 7:
        return None

    normalized = stripped.lower()

    for category, patterns in SECTION_CATEGORIES.items():
        for pattern in patterns:
            if normalized == pattern:
                return category
            if (normalized.startswith(pattern)
                    and len(normalized) < len(pattern) + 15):
                return category

    # All-caps lines are common CV section headers
    if stripped.isupper() and len(stripped) < 45:
        for category, patterns in SECTION_CATEGORIES.items():
            for pattern in patterns:
                if pattern in normalized:
                    return category

    return None


def parse_sections(text: str) -> list[dict]:
    """
    Split CV text into sections.

    Returns list of:
        {"category": str, "header": str, "lines": [str]}
    """
    lines = text.splitlines()
    sections: list[dict] = []
    current: dict = {"category": "general", "header": "", "lines": []}

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        cat = classify_section_header(stripped)
        if cat is not None:
            if current["lines"]:
                sections.append(current)
            current = {"category": cat, "header": stripped, "lines": []}
        else:
            current["lines"].append(stripped)

    if current["lines"]:
        sections.append(current)

    return sections


def _extract_context_label(lines: list[str]) -> str:
    """Try to extract a company/project name from leading lines."""
    for line in lines[:4]:
        clean = _BULLET_PREFIX_RE.sub('', line).strip()
        if not clean or len(clean) < 3:
            continue
        # Bullet-point content lines are not labels
        if any(clean.lower().startswith(v) for v in HIGH_EXEC_VERBS[:20]):
            continue
        # Short non-bullet lines before content are often labels
        if len(clean) < 70 and not clean[0].islower():
            # Remove date portions for cleaner labels
            label = _YEAR_RE.sub('', clean).strip().rstrip('|–—-,').strip()
            if label and len(label) >= 3:
                return label[:60]
    return ""


# ── Evidence item extraction ──────────────────────────────────

def extract_evidence_items(sections: list[dict]) -> list[dict]:
    """
    Extract candidate evidence items from parsed sections.

    Each item has: trace_id, source_section, source_label,
    bullet_text, raw_section_header.
    """
    items: list[dict] = []
    counter = 0

    for section in sections:
        cat = section["category"]
        if cat not in EVIDENCE_SECTIONS:
            continue

        label = _extract_context_label(section["lines"])
        current_sub_label = label

        for line in section["lines"]:
            clean = _BULLET_PREFIX_RE.sub('', line).strip()

            # Skip very short lines (dates, sub-headers)
            if len(clean) < 18:
                # But might be a sub-label (company/project name)
                if (3 < len(clean) < 65 and not clean[0].islower()
                        and not _YEAR_RE.fullmatch(clean.strip())):
                    current_sub_label = clean[:60]
                continue

            # Skip lines that are clearly just dates or locations
            if _YEAR_RE.fullmatch(clean.strip()):
                continue
            if len(clean.split()) <= 3 and _YEAR_RE.search(clean):
                continue

            counter += 1
            items.append({
                "trace_id": f"ev_{counter:03d}",
                "source_section": cat,
                "source_label": current_sub_label or label,
                "bullet_text": clean[:200],
                "raw_section_header": section["header"],
            })

    return items


# ── Evidence scoring ──────────────────────────────────────────

def _count_matches(text_lower: str, word_list: list[str]) -> int:
    return sum(1 for w in word_list if w in text_lower)


def _skill_relevance(
    text_lower: str,
    required_skills: list[dict],
    nice_skills: list[dict],
    skill_dict: dict,
) -> tuple[float, list[str]]:
    """Score how relevant a bullet is to the target role's skills."""
    matched: list[str] = []

    # Check against job-required skill names and their variants
    all_job_skills: dict[str, list[str]] = {}
    for s in required_skills + nice_skills:
        name = s["name"].lower()
        variants = skill_dict.get(name, [name])
        all_job_skills[name] = [v.lower() for v in variants] + [name]

    for canonical, variants in all_job_skills.items():
        if any(v in text_lower for v in variants):
            matched.append(canonical)

    if not matched:
        return 0.0, []

    # Weight required skills higher than nice-to-have
    required_names = {s["name"].lower() for s in required_skills}
    score = 0.0
    for m in matched:
        score += 0.35 if m in required_names else 0.15
    return min(1.0, score), matched


def score_evidence_item(
    item: dict,
    required_skills: list[dict],
    nice_skills: list[dict],
    skill_dict: dict,
) -> dict:
    """
    Score an evidence item for execution, ownership, and role relevance.
    Returns the item dict augmented with scores.
    """
    text = item["bullet_text"].lower()

    # Execution score
    high_hits = _count_matches(text, HIGH_EXEC_VERBS)
    mod_hits = _count_matches(text, MODERATE_EXEC_VERBS)
    deliv_hits = _count_matches(text, DELIVERABLE_KEYWORDS)
    weak_hits = _count_matches(text, WEAK_SIGNALS)

    exec_score = min(1.0, high_hits * 0.25 + mod_hits * 0.10 + deliv_hits * 0.15)
    # Penalize weak/passive phrasing
    if weak_hits > 0 and high_hits == 0:
        exec_score *= 0.4

    # Ownership score
    own_hits = _count_matches(text, OWNERSHIP_SIGNALS)
    own_score = min(1.0, own_hits * 0.30)
    # Professional sections get slight ownership boost
    if item["source_section"] in ("experience", "internship"):
        own_score = min(1.0, own_score + 0.10)

    # Role relevance
    relevance, matched_skills = _skill_relevance(
        text, required_skills, nice_skills, skill_dict)

    # Combined strength
    strength = (exec_score * 0.35 + own_score * 0.20 + relevance * 0.45)

    # Floor: items from experience/internship with any execution verb
    # are inherently more valuable than random lines
    if (item["source_section"] in ("experience", "internship")
            and (high_hits > 0 or deliv_hits > 0)):
        strength = max(strength, 0.18)

    eligible = strength >= 0.12

    return {
        **item,
        "execution_score": round(exec_score, 3),
        "ownership_score": round(own_score, 3),
        "role_relevance": round(relevance, 3),
        "skill_matches": matched_skills,
        "evidence_strength": round(strength, 3),
        "eligible": eligible,
    }


# ── Ranking and selection ─────────────────────────────────────

def rank_and_select(scored_items: list[dict], top_n: int = 8) -> list[dict]:
    """Rank eligible items by evidence strength, deduplicate, select top N."""
    eligible = [i for i in scored_items if i["eligible"]]
    eligible.sort(key=lambda x: x["evidence_strength"], reverse=True)

    # Deduplicate: skip items that are too similar to already-selected ones
    selected: list[dict] = []
    seen_texts: list[str] = []

    for item in eligible:
        text_norm = item["bullet_text"].lower().strip()
        # Simple dedup: skip if >60% word overlap with any selected
        words = set(text_norm.split())
        is_dup = False
        for prev in seen_texts:
            prev_words = set(prev.split())
            if len(words) > 0 and len(prev_words) > 0:
                overlap = len(words & prev_words) / max(len(words), len(prev_words))
                if overlap > 0.6:
                    is_dup = True
                    break
        if is_dup:
            continue

        selected.append(item)
        seen_texts.append(text_norm)
        if len(selected) >= top_n:
            break

    return selected


# ── Deterministic summarization ───────────────────────────────

def _confidence_level(items: list[dict]) -> str:
    if not items:
        return "none"
    avg = sum(i["evidence_strength"] for i in items) / len(items)
    if avg >= 0.45 and len(items) >= 3:
        return "strong"
    if avg >= 0.20 and len(items) >= 2:
        return "moderate"
    return "limited"


def summarize_deterministic(
    selected: list[dict],
    job: dict,
) -> dict:
    """
    Build curated evidence summary from ranked items.

    Produces 1–2 concise, recruiter-friendly sentences for the visible
    PDF report. Internal metadata (counts, IDs, confidence) is kept in
    the returned dict but never surfaces in the summary text.
    """
    if not selected:
        return {
            "summary_lines": [
                "No strong applied evidence identified from the available "
                "profile. Practical capability should be explored in interview."
            ],
            "source_count": 0,
            "matched_skills": [],
            "confidence": "none",
            "caution_note": "Evidence base insufficient for assessment",
            "sections_covered": [],
            "supporting_ids": [],
            "top_items": [],
        }

    sections = sorted(set(i["source_section"] for i in selected))
    all_skills = sorted(set(
        s for i in selected for s in i.get("skill_matches", [])))
    conf = _confidence_level(selected)
    n = len(selected)

    has_prof = any(s in ("experience", "internship") for s in sections)
    has_acad = any(
        s in ("projects", "education", "research", "competitions")
        for s in sections)

    # ── Build 1–2 readable sentences ──────────────────────────

    # Human-readable skill names (e.g. financial_modeling → financial modeling)
    display_skills = [s.replace("_", " ") for s in all_skills]

    # Skill phrase
    if display_skills:
        if len(display_skills) == 1:
            skill_phrase = display_skills[0]
        elif len(display_skills) == 2:
            skill_phrase = f"{display_skills[0]} and {display_skills[1]}"
        else:
            skill_phrase = ", ".join(display_skills[:3])
    else:
        skill_phrase = ""

    # Context phrase
    if has_prof and has_acad:
        ctx = "across professional and academic work"
    elif has_prof:
        ctx = "in professional settings"
    elif has_acad:
        ctx = "in academic and project-based work"
    else:
        ctx = "across available profile experience"

    # Sentence 1: what the evidence supports
    if skill_phrase and conf in ("strong", "moderate"):
        sent1 = (
            f"Strongest evidence supports {skill_phrase} capability "
            f"{ctx}."
        )
    elif skill_phrase:
        sent1 = (
            f"The clearest signals point to {skill_phrase}, "
            f"primarily {ctx}."
        )
    elif conf in ("strong", "moderate"):
        sent1 = (
            f"Relevant applied work is visible {ctx}, though "
            f"specific skill alignment is limited."
        )
    else:
        sent1 = (
            f"Applied evidence is limited {ctx}."
        )

    # Sentence 2: validation note (only when needed)
    if conf == "strong":
        sent2 = "Execution depth should be confirmed through targeted questions."
    elif conf == "moderate":
        sent2 = "Practical depth should be validated in interview."
    else:
        sent2 = (
            "Hands-on capability should be explored through focused "
            "interview questioning."
        )

    lines = [f"{sent1} {sent2}"]

    # ── Internal metadata (not shown in PDF copy) ─────────────

    caution = None
    if conf == "limited":
        caution = (
            "Evidence base is narrow; findings should be weighted "
            "accordingly in the overall assessment")
    elif not has_prof:
        caution = (
            "No professional experience evidence detected; academic "
            "and project signals carry the assessment")

    return {
        "summary_lines": lines,
        "source_count": n,
        "matched_skills": all_skills,
        "confidence": conf,
        "caution_note": caution,
        "sections_covered": sections,
        "supporting_ids": [i["trace_id"] for i in selected],
        "top_items": [
            {
                "bullet_text": i["bullet_text"],
                "source_section": i["source_section"],
                "source_label": i["source_label"],
                "evidence_strength": i["evidence_strength"],
            }
            for i in selected[:5]
        ],
    }


# ── Optional Groq-assisted summarization ──────────────────────

EVIDENCE_SYSTEM_PROMPT = """\
You write the "Applied Evidence" line for a premium hiring assessment report.

Given a candidate's evidence items and the target role, produce exactly \
1-2 short sentences that a recruiter can scan in seconds.

Format: state the strongest skill areas, where the evidence comes from \
(professional, academic, project work), and a brief validation note.

Rules:
- Maximum 2 sentences total
- Name concrete skill areas (e.g. "analysis and financial modeling")
- Use broad source context (e.g. "across professional and academic work")
- End with a short validation note if relevant (e.g. "Practical depth \
should be validated in interview.")
- Calm, direct, executive tone
- Do NOT mention signal counts, source categories, traceability, or \
pipeline mechanics
- Do NOT invent skills or evidence not in the items
- Do NOT use first person
- Return ONLY a JSON array of 1-2 strings, no markdown

Good example:
["Strongest evidence supports analysis and financial modeling capability \
across professional and academic work. Practical depth should be validated \
in interview."]
"""


def summarize_with_groq(
    selected: list[dict],
    job: dict,
    ai_client,
) -> list[str] | None:
    """
    Attempt Groq-assisted evidence summarization.
    Returns list of summary strings, or None on failure.
    """
    if not ai_client or not getattr(ai_client, 'enabled', False):
        return None

    import json

    evidence_lines = []
    for i, item in enumerate(selected[:6], 1):
        section = item["source_section"].title()
        label = item.get("source_label", "")
        text = item["bullet_text"]
        label_part = f" | {label}" if label else ""
        evidence_lines.append(f"{i}. [{section}{label_part}] {text}")

    skills_str = ", ".join(
        s["name"] for s in job.get("required_skills", [])[:6])
    nice_str = ", ".join(
        s["name"] for s in job.get("nice_to_have_skills", [])[:4])

    user_msg = json.dumps({
        "target_role": f"{job.get('title', '')} at {job.get('company', '')}",
        "required_skills": skills_str,
        "nice_to_have_skills": nice_str,
        "evidence_items": evidence_lines,
    }, default=str)

    try:
        resp = ai_client.client.chat.completions.create(
            model=ai_client.model,
            messages=[
                {"role": "system", "content": EVIDENCE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=400,
            timeout=10,
        )
        text = resp.choices[0].message.content.strip()
        result = json.loads(text)
        if isinstance(result, list) and len(result) >= 2:
            return [str(s) for s in result[:3]]
        log.warning("Groq evidence summary returned invalid format: %s", text)
        return None
    except Exception as e:
        log.warning("Groq evidence summarization failed: %s", e)
        return None


# ── Main entry point ──────────────────────────────────────────

def evaluate_evidence(
    extraction: dict,
    job: dict,
    ai_client=None,
    skill_dict: dict | None = None,
) -> dict:
    """
    Main evidence evaluation pipeline.

    Takes extraction dict (with evidence_items) and job dict,
    returns curated evidence summary for the PDF report.

    Falls back gracefully at every step:
    - If evidence_items missing → uses legacy projects field
    - If Groq unavailable → deterministic summarization
    - If no evidence at all → professional "limited evidence" output
    """
    from .extraction import DEFAULT_SKILL_DICTIONARY
    skill_dict = skill_dict or DEFAULT_SKILL_DICTIONARY

    required_skills = job.get("required_skills", [])
    nice_skills = job.get("nice_to_have_skills", [])

    # Get evidence items (new path) or build from legacy projects
    raw_items = extraction.get("evidence_items", [])

    if not raw_items:
        # Fallback: convert legacy projects into minimal evidence items
        legacy = extraction.get("projects", [])
        for idx, proj in enumerate(legacy):
            raw_items.append({
                "trace_id": f"legacy_{idx:03d}",
                "source_section": "projects",
                "source_label": "",
                "bullet_text": proj[:200],
                "raw_section_header": "Projects",
            })

    # Score all items
    scored = [
        score_evidence_item(item, required_skills, nice_skills, skill_dict)
        for item in raw_items
    ]

    # Rank and select
    selected = rank_and_select(scored, top_n=8)

    # Summarize
    groq_lines = None
    if ai_client and selected:
        groq_lines = summarize_with_groq(selected, job, ai_client)

    result = summarize_deterministic(selected, job)

    # If Groq produced better summaries, use them but keep deterministic metadata
    if groq_lines:
        result["summary_lines"] = groq_lines
        result["groq_assisted"] = True
    else:
        result["groq_assisted"] = False

    return result
