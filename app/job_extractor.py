"""Extract structured job data from a real job posting URL."""

import re
from html.parser import HTMLParser

import httpx

# ---------------------------------------------------------------------------
# Expanded skill dictionary for job descriptions (broader than CV extraction)
# ---------------------------------------------------------------------------
SKILL_MAP = {
    "excel": ["excel", "spreadsheet", "vba", "pivot table", "vlookup"],
    "powerpoint": ["powerpoint", "ppt", "presentation", "slides", "keynote"],
    "sql": ["sql", "mysql", "postgres", "postgresql", "sqlite", "database", "bigquery", "redshift"],
    "analysis": ["analysis", "analytical", "data analysis", "research", "quantitative"],
    "financial_modeling": ["financial model", "valuation", "dcf", "lbo", "modeling",
                           "financial analysis", "forecasting", "budgeting"],
    "python": ["python", "pandas", "numpy", "jupyter", "scikit"],
    "r_lang": ["r programming", " r ", "rstudio", "tidyverse", "ggplot"],
    "tableau": ["tableau", "power bi", "powerbi", "data visualization", "looker"],
    "communication": ["communication", "stakeholder management", "presentation skills",
                       "client facing", "client-facing"],
    "project_management": ["project management", "agile", "scrum", "jira", "project planning"],
    "java": ["java ", "java,", "spring boot", "spring framework"],
    "javascript": ["javascript", "typescript", "react", "angular", "node.js", "nodejs"],
    "cloud": ["aws", "azure", "gcp", "google cloud", "cloud computing"],
    "machine_learning": ["machine learning", "deep learning", "ai ", "artificial intelligence",
                          "neural network", "nlp", "natural language"],
    "marketing": ["marketing", "seo", "sem", "google analytics", "campaign",
                   "social media", "content strategy", "brand"],
    "accounting": ["accounting", "gaap", "ifrs", "bookkeeping", "audit", "tax"],
    "sales": ["sales", "crm", "salesforce", "hubspot", "business development", "lead generation"],
    "design": ["figma", "sketch", "adobe", "photoshop", "illustrator", "ui/ux", "ux design"],
}

CONTEXT_SIGNALS = [
    "corporate", "stakeholder", "matrix", "startup", "regulated",
    "cross-functional", "creative", "tech", "consulting", "enterprise",
    "b2b", "b2c", "fast-paced", "collaborative", "autonomous",
]

LOCATION_KEYWORDS = {
    "remote": ["remote", "work from home", "wfh", "anywhere", "fully remote",
               "100% remote", "distributed"],
    "hybrid": ["hybrid", "flexible", "partly remote", "2-3 days"],
    "onsite": ["onsite", "on-site", "in-office", "office-based", "in person", "on site"],
}

# Sections we try to identify in job pages
SECTION_PATTERNS = {
    "requirements": r"(?:requirements?|qualifications?|what (?:you|we)(?:'re| are) looking for|"
                    r"who you are|must[- ]have|minimum qualifications?|what you(?:'ll)? need|"
                    r"skills (?:&|and) (?:experience|qualifications))",
    "responsibilities": r"(?:responsibilities|what you(?:'ll| will) do|key duties|"
                        r"the role|about the role|your (?:role|impact)|day[- ]to[- ]day)",
    "nice_to_have": r"(?:nice[- ]to[- ]have|preferred|bonus|plus|ideally|"
                    r"preferred qualifications?|additional skills?)",
    "benefits": r"(?:benefits?|perks|what we offer|compensation|why (?:join|work))",
}


# ---------------------------------------------------------------------------
# HTML text extraction (no external dependencies like bs4)
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Simple HTML→text extractor that skips script/style tags."""
    SKIP_TAGS = {"script", "style", "noscript", "svg", "path", "meta", "link", "head"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip += 1
        if tag.lower() in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr", "section"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        # Collapse whitespace within lines, keep newlines
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        # Remove blank runs
        result = []
        blank_count = 0
        for ln in lines:
            if not ln:
                blank_count += 1
                if blank_count <= 2:
                    result.append("")
            else:
                blank_count = 0
                result.append(ln)
        return "\n".join(result).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_text()


# ---------------------------------------------------------------------------
# Job content extraction
# ---------------------------------------------------------------------------
def _detect_location(text: str) -> str:
    t = text.lower()
    for policy, keywords in LOCATION_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return policy
    return "onsite"


def _extract_title(text: str) -> str:
    """Try to find the job title from the first few lines."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()][:20]
    title_patterns = [
        r"^((?:senior |junior |lead |staff |principal |intern(?:ship)? )?[\w /&,.-]{3,80}(?:analyst|engineer|developer|manager|designer|consultant|intern|coordinator|specialist|associate|director|assistant))",
    ]
    for ln in lines:
        for pat in title_patterns:
            m = re.search(pat, ln, re.IGNORECASE)
            if m:
                return m.group(1).strip()[:200]
    # Fallback: first non-trivial line
    for ln in lines:
        if 10 < len(ln) < 200 and not ln.startswith("http"):
            return ln[:200]
    return ""


def _extract_company(text: str, source_domain: str) -> str:
    """Try to find the company name."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()][:30]
    # Look for patterns like "at Company", "Company is hiring", "About Company"
    for ln in lines:
        m = re.search(r"(?:at|@)\s+([A-Z][\w &.,'-]{2,60})", ln)
        if m:
            return m.group(1).strip()[:100]
        m = re.search(r"^((?:[A-Z][\w]*\s?){1,5})\s+is\s+(?:hiring|looking|seeking)", ln)
        if m:
            return m.group(1).strip()[:100]
        m = re.search(r"^About\s+((?:[A-Z][\w]*\s?){1,5})", ln)
        if m:
            return m.group(1).strip()[:100]
    # Fallback: clean domain
    domain = source_domain.replace("www.", "").split(".")[0]
    return domain.title() if domain else "Unknown"


def _extract_section(text: str, pattern_key: str) -> str:
    """Extract text following a section header."""
    pattern = SECTION_PATTERNS.get(pattern_key, "")
    if not pattern:
        return ""
    # Find the section header
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    # Capture until the next section header or end
    all_patterns = "|".join(SECTION_PATTERNS.values())
    next_section = re.search(all_patterns, text[start:], re.IGNORECASE | re.MULTILINE)
    end = start + next_section.start() if next_section else min(start + 3000, len(text))
    return text[start:end].strip()


def _extract_skills_from_text(text: str) -> list[dict]:
    """Extract skills from full job description text with tiering."""
    t = text.lower()
    skills = []
    seen = set()

    # Try to find requirements section for "must-have"
    req_text = _extract_section(text, "requirements").lower()
    nice_text = _extract_section(text, "nice_to_have").lower()

    for canonical, variants in SKILL_MAP.items():
        if canonical in seen:
            continue
        in_req = any(v in req_text for v in variants) if req_text else False
        in_nice = any(v in nice_text for v in variants) if nice_text else False
        in_full = any(v in t for v in variants)

        if in_req:
            skills.append({"name": canonical, "tier": "must", "weight": 3})
            seen.add(canonical)
        elif in_nice:
            skills.append({"name": canonical, "tier": "nice", "weight": 1})
            seen.add(canonical)
        elif in_full:
            skills.append({"name": canonical, "tier": "must", "weight": 2})
            seen.add(canonical)

    return skills


def _extract_context_keywords(text: str) -> list[str]:
    t = text.lower()
    return [kw for kw in CONTEXT_SIGNALS if kw in t]


def _assess_extraction_quality(data: dict) -> dict:
    """Score the extraction quality to decide if we have enough for a report.

    Also sets `data_confidence` ("strong", "adequate", "weak") which
    downstream scoring uses to decide whether results are trustworthy.
    """
    score = 0
    reasons = []

    if data.get("title"):
        score += 20
    else:
        reasons.append("no title found")

    if data.get("company") and data["company"] != "Unknown":
        score += 10

    if data.get("description") and len(data["description"]) > 200:
        score += 25
    elif data.get("description") and len(data["description"]) > 50:
        score += 10
    else:
        reasons.append("description too short")

    num_skills = len(data.get("required_skills", []))
    if num_skills >= 3:
        score += 25
    elif num_skills >= 1:
        score += 10
    else:
        reasons.append("no skills identified")

    if data.get("requirements") and len(data["requirements"]) > 30:
        score += 10
    else:
        reasons.append("no requirements section")

    if data.get("responsibilities") and len(data["responsibilities"]) > 30:
        score += 10

    data["extraction_quality"] = score

    # Quality gate: need at least 50 to generate a report
    data["quality_sufficient"] = score >= 50
    if not data["quality_sufficient"]:
        data["quality_reasons"] = reasons

    # Data confidence for scoring differentiation
    if score >= 70 and num_skills >= 3:
        data["data_confidence"] = "strong"
    elif score >= 50 and num_skills >= 1:
        data["data_confidence"] = "adequate"
    else:
        data["data_confidence"] = "weak"

    return data


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------
async def extract_job_from_url(url: str) -> dict:
    """Fetch a job URL and extract structured job data.

    Returns a dict with job fields + extraction_quality + quality_sufficient.
    """
    if not url or not url.startswith("http"):
        return {"error": "Invalid URL.", "quality_sufficient": False}

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SOCLAWBot/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            resp = await client.get(url)

        if resp.status_code != 200:
            return {
                "error": f"Could not fetch page (HTTP {resp.status_code}).",
                "quality_sufficient": False,
            }

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return {
                "error": "Page is not HTML. Cannot extract job details.",
                "quality_sufficient": False,
            }

        html = resp.text
        if len(html) > 500_000:
            html = html[:500_000]  # safety limit

        text = html_to_text(html)

        # Truncate very long pages
        if len(text) > 15_000:
            text = text[:15_000]

        from urllib.parse import urlparse
        domain = urlparse(url).netloc

        title = _extract_title(text)
        company = _extract_company(text, domain)
        location_policy = _detect_location(text)
        description = text[:5000]
        requirements = _extract_section(text, "requirements")[:3000]
        responsibilities = _extract_section(text, "responsibilities")[:3000]
        nice_to_have = _extract_section(text, "nice_to_have")[:2000]

        required_skills = _extract_skills_from_text(text)
        nice_skills = [s for s in required_skills if s["tier"] == "nice"]
        must_skills = [s for s in required_skills if s["tier"] != "nice"]

        context_keywords = _extract_context_keywords(text)

        data = {
            "title": title,
            "company": company,
            "location_policy": location_policy,
            "description": description,
            "requirements": requirements,
            "responsibilities": responsibilities,
            "required_skills": must_skills,
            "nice_to_have_skills": nice_skills,
            "context_keywords": context_keywords,
            "source_url": url,
            "source": domain,
            "error": None,
        }

        return _assess_extraction_quality(data)

    except httpx.TimeoutException:
        return {"error": "Page took too long to load.", "quality_sufficient": False}
    except httpx.ConnectError:
        return {"error": "Could not connect to the page.", "quality_sufficient": False}
    except Exception as e:
        return {"error": f"Extraction failed: {str(e)}", "quality_sufficient": False}
