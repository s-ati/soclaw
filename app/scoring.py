from dataclasses import dataclass
from typing import Any


def normalize_likert(avg_1_to_5: float) -> float:
    return ((avg_1_to_5 - 1.0) / 4.0) * 100.0


def reverse_likert(x: int) -> int:
    return 6 - x


def clamp01(x: float) -> float:
    return max(0.0, min(100.0, x))


DEFAULT_WEIGHTS_CORP_INTERN = {"S":0.25,"O":0.20,"W":0.18,"A":0.15,"C":0.12,"L":0.10}


@dataclass
class ScoreResult:
    fits: dict
    risks: dict
    match_score: int
    recommendation: str
    flags: list[str]
    interview_questions: list[str]


def compute_skill_fit(extraction: dict, job_required: list[dict], job_nice: list[dict]) -> tuple[float, list[str]]:
    skills = set(extraction.get("skills", []))
    missing_must = []

    total_w = 0.0
    matched_w = 0.0

    for s in job_required:
        name = s["name"]
        weight = float(s.get("weight", 1))
        total_w += weight
        if name in skills:
            matched_w += weight
        else:
            if s.get("tier") == "must" and int(s.get("weight", 1)) >= 3:
                missing_must.append(name)

    if total_w <= 0:
        base = 0.0
    else:
        base = 100.0 * (matched_w / total_w)

    nice_matched = 0
    for s in job_nice:
        if s["name"] in skills:
            nice_matched += 1

    bonus = min(10.0, 2.0 * nice_matched)
    return clamp01(base + bonus), missing_must


def compute_context_cv(extraction: dict) -> float:
    # keyword-based context signal (corporate-ish indicators)
    keys = set(k.lower() for k in extraction.get("context_keywords", []))
    score = 0.0
    if any(k in keys for k in ["intern", "internship", "working student", "werkstudent", "practicum"]):
        score += 25
    if any(k in keys for k in ["gmbh", "ag", "inc", "ltd"]):
        score += 25
    if any(k in keys for k in ["stakeholder", "cross-functional", "reporting line", "matrix"]):
        score += 25
    # if any corporate keywords at all:
    if len(keys) >= 3:
        score += 25
    return clamp01(score)


def compute_location_structured(job_location_policy: str, remote_only: bool) -> float:
    policy = (job_location_policy or "onsite").lower()
    if policy == "onsite":
        return 0.0 if remote_only else 100.0
    if policy == "hybrid":
        return 0.0 if remote_only else 80.0
    if policy == "remote":
        return 100.0
    return 80.0


def weight_sum(fits: dict, weights: dict) -> float:
    # Normalize weights if needed
    s = sum(float(weights.get(k, 0.0)) for k in ["S","O","C","A","W","L"])
    if s <= 0:
        weights = DEFAULT_WEIGHTS_CORP_INTERN
        s = 1.0
    wnorm = {k: float(weights.get(k, 0.0))/s for k in ["S","O","C","A","W","L"]}
    return sum(wnorm[k] * float(fits[k]) for k in wnorm)


def label_recommendation(match_score: int) -> str:
    if match_score < 40:
        return "NOT RECOMMENDED"
    if match_score < 60:
        return "RISK APPLICATION"
    if match_score < 75:
        return "SOLID FIT"
    return "TOP CANDIDATE"


def risk_level(risk: float) -> str:
    if risk < 25:
        return "Low"
    if risk < 50:
        return "Moderate"
    return "Elevated"


def build_static_interview_questions() -> list[str]:
    return [
        "Skill: Walk me through a structured problem you solved under time pressure. What was your approach?",
        "Ownership: Tell me about a time you started something without being asked. What happened?",
        "Work style: How do you manage progress updates and blockers when multiple stakeholders are involved?",
        "Adaptability: Describe a time requirements changed. How did you adjust and what did you learn?",
    ]


def deterministic_tailored_questions(top_risks: list[str], missing_must: list[str]) -> list[str]:
    qs = []
    for r in top_risks:
        if r == "Skill" and missing_must:
            qs.append(f"Skill validation: Your profile did not clearly show {', '.join(missing_must[:2])}. Can you provide concrete examples of using these?")
        elif r == "Context":
            qs.append("Context validation: Tell me about your exposure to structured organizations and formal reporting lines. What was difficult?")
        elif r == "Ownership":
            qs.append("Ownership validation: Describe a time you owned a deliverable end-to-end without close supervision. How did you ensure quality?")
        elif r == "Work Style":
            qs.append("Work style validation: How do you prioritize when you receive conflicting requests from two stakeholders?")
        elif r == "Adaptability":
            qs.append("Adaptability validation: When feedback contradicts your initial approach, how do you decide what to change and what to keep?")
        elif r == "Location":
            qs.append("Location validation: Confirm onsite/hybrid availability and any constraints that could affect day-to-day presence.")
    return qs[:3]


def compute_socawl(
    extraction: dict,
    questionnaire: dict,
    linkedin_url: str | None,
    job: dict,
    remote_only: bool,
    ai_questions: list[str] | None = None,
) -> ScoreResult:
    # questionnaire ints
    Q1 = int(questionnaire["Q1"]); Q2 = int(questionnaire["Q2"]); Q3 = int(questionnaire["Q3"]); Q4 = int(questionnaire["Q4"])
    Q5 = int(questionnaire["Q5"]); Q6 = int(questionnaire["Q6"]); Q7 = int(questionnaire["Q7"]); Q8 = int(questionnaire["Q8"])

    O = clamp01(normalize_likert((Q1 + reverse_likert(Q2))/2.0))
    A = clamp01(normalize_likert((Q3 + Q4)/2.0))
    W = clamp01(normalize_likert((Q5 + Q6)/2.0))
    C_behavioral = clamp01(normalize_likert(float(Q7)))
    L_behavioral = clamp01(normalize_likert(float(Q8)))

    S, missing_must = compute_skill_fit(extraction, job["required_skills"], job.get("nice_to_have_skills", []))

    C_cv = compute_context_cv(extraction)
    C = clamp01(0.6 * C_cv + 0.4 * C_behavioral)

    L_struct = compute_location_structured(job["location_policy"], remote_only=remote_only)
    L = clamp01(0.8 * L_struct + 0.2 * L_behavioral)

    fits = {"S":S,"O":O,"C":C,"A":A,"W":W,"L":L}
    risks = {k: clamp01(100.0 - v) for k, v in fits.items()}

    # Match
    match = weight_sum(fits, job.get("weights") or DEFAULT_WEIGHTS_CORP_INTERN)
    flags: list[str] = []

    # Hard stops (high quality defaults)
    if L_struct == 0.0:
        risks["L"] = max(risks["L"], 90.0)
        match = min(match, 60.0)
        flags.append("location_incompatible")

    if missing_must or fits["S"] < 40.0:
        risks["S"] = max(risks["S"], 85.0)
        match = min(match, 55.0)
        flags.append("critical_skill_gap")

    if fits["O"] < 30.0:
        risks["O"] = max(risks["O"], 80.0)
        match = min(match, 60.0)
        flags.append("low_ownership")

    if fits["W"] < 30.0:
        risks["W"] = max(risks["W"], 80.0)
        match = min(match, 60.0)
        flags.append("low_workstyle")

    match_int = int(round(match))
    recommendation = label_recommendation(match_int)

    # Interview questions: 4 static + 3 tailored (AI optional)
    static_qs = build_static_interview_questions()

    # Select top risk dimensions (excluding Location unless flagged)
    risk_map = {"S":"Skill","O":"Ownership","C":"Context","A":"Adaptability","W":"Work Style","L":"Location"}
    ranked = sorted(risks.items(), key=lambda kv: kv[1], reverse=True)
    top_dims = []
    for dim, val in ranked:
        name = risk_map[dim]
        if name == "Location" and "location_incompatible" not in flags:
            continue
        top_dims.append(name)
        if len(top_dims) >= 2:
            break

    tailored = []
    if ai_questions and len(ai_questions) >= 3:
        tailored = ai_questions[:3]
    else:
        tailored = deterministic_tailored_questions(top_dims, missing_must)

    interview_questions = static_qs + tailored

    return ScoreResult(
        fits=fits,
        risks=risks,
        match_score=match_int,
        recommendation=recommendation,
        flags=flags,
        interview_questions=interview_questions
    )
