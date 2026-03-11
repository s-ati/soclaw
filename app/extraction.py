import re
from typing import Iterable

DEFAULT_SKILL_DICTIONARY = {
    "excel": ["excel", "ms excel", "spreadsheet", "vba"],
    "powerpoint": ["powerpoint", "ppt", "slides", "deck"],
    "sql": ["sql", "mysql", "postgres", "sqlite"],
    "analysis": ["analysis", "analytical", "data analysis"],
    "financial_modeling": ["financial model", "modeling", "valuation", "dcf"],
    "python": ["python", "pandas", "numpy"],
}

COURSE_KEYWORDS = [
    "corporate finance", "statistics", "econometrics", "strategy", "accounting",
    "data science", "machine learning", "microeconomics", "macroeconomics"
]

PROJECT_KEYWORDS = [
    "project", "case", "thesis", "capstone", "competition", "initiative"
]

CONTEXT_KEYWORDS = [
    "intern", "internship", "working student", "werkstudent", "practicum",
    "gmbh", "ag", "inc", "ltd", "stakeholder", "cross-functional", "reporting line", "matrix"
]


def normalize_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def find_any(text_norm: str, variants: Iterable[str]) -> bool:
    return any(v in text_norm for v in variants)


def extract_structured(text: str, skill_dict: dict | None = None) -> dict:
    skill_dict = skill_dict or DEFAULT_SKILL_DICTIONARY
    t = normalize_token(text)

    skills_found = []
    for canonical, variants in skill_dict.items():
        variants_norm = [normalize_token(v) for v in variants]
        if find_any(t, variants_norm):
            skills_found.append(canonical)

    courses_found = []
    for c in COURSE_KEYWORDS:
        if normalize_token(c) in t:
            courses_found.append(c)

    # Legacy project detection (kept for backward compatibility)
    projects_found = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        ln_norm = normalize_token(ln)
        if any(k in ln_norm for k in PROJECT_KEYWORDS):
            label = ln.strip()
            if len(label) > 80:
                label = label[:77] + "..."
            projects_found.append(label)
        if len(projects_found) >= 6:
            break

    context_hits = []
    for k in CONTEXT_KEYWORDS:
        if normalize_token(k) in t:
            context_hits.append(k)

    # Section-aware evidence extraction
    from .evidence import parse_sections, extract_evidence_items
    sections = parse_sections(text)
    evidence_items = extract_evidence_items(sections)

    return {
        "skills": sorted(set(skills_found)),
        "courses": sorted(set(courses_found))[:10],
        "projects": projects_found[:6],
        "context_keywords": sorted(set(context_hits))[:20],
        "evidence_items": evidence_items,
    }
