"""Fetch structured job postings from Greenhouse and Lever public APIs.

Both APIs are free, require no API keys, and return real structured job data.
This module handles fetching, normalization, and keyword-based filtering.
"""

import asyncio
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

# ---------------------------------------------------------------------------
# In-memory cache for fetched jobs (avoids re-fetching on every search)
# ---------------------------------------------------------------------------
_job_cache: dict = {"jobs": [], "ts": 0.0}
_CACHE_TTL = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Source configuration — add companies here to expand the searchable universe
# ---------------------------------------------------------------------------

# Greenhouse: board tokens (the slug in boards.greenhouse.io/{token})
GREENHOUSE_BOARDS = [
    {"token": "airbnb", "company": "Airbnb"},
    {"token": "figma", "company": "Figma"},
    {"token": "stripe", "company": "Stripe"},
    {"token": "notion", "company": "Notion"},
    {"token": "airtable", "company": "Airtable"},
    {"token": "duolingo", "company": "Duolingo"},
    {"token": "plaid", "company": "Plaid"},
    {"token": "gusto", "company": "Gusto"},
    {"token": "brex", "company": "Brex"},
    {"token": "ramp", "company": "Ramp"},
    {"token": "benchling", "company": "Benchling"},
    {"token": "verkada", "company": "Verkada"},
    {"token": "anduril", "company": "Anduril"},
    {"token": "relativityspace", "company": "Relativity Space"},
    {"token": "watershedclimate", "company": "Watershed"},
    {"token": "vanta", "company": "Vanta"},
    {"token": "retool", "company": "Retool"},
    {"token": "rippling", "company": "Rippling"},
    {"token": "scaleai", "company": "Scale AI"},
    {"token": "databricks", "company": "Databricks"},
]

# Lever: company slugs (the slug in jobs.lever.co/{slug})
LEVER_SITES = [
    {"slug": "netflix", "company": "Netflix"},
    {"slug": "atlassian", "company": "Atlassian"},
    {"slug": "openai", "company": "OpenAI"},
    {"slug": "coinbase", "company": "Coinbase"},
    {"slug": "reddit", "company": "Reddit"},
    {"slug": "figma", "company": "Figma"},
    {"slug": "palantir", "company": "Palantir"},
    {"slug": "robinhood", "company": "Robinhood"},
    {"slug": "spotify", "company": "Spotify"},
    {"slug": "canva", "company": "Canva"},
]


# ---------------------------------------------------------------------------
# HTML → text helper (reuse the same approach as job_extractor)
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "path", "meta", "link", "head"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip += 1
        if tag.lower() in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr", "section", "ul", "ol"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        result = []
        blank = 0
        for ln in lines:
            if not ln:
                blank += 1
                if blank <= 1:
                    result.append("")
            else:
                blank = 0
                result.append(ln)
        return "\n".join(result).strip()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_text()


# ---------------------------------------------------------------------------
# Normalized job shape
# ---------------------------------------------------------------------------

def _detect_work_mode(text: str, location: str) -> str:
    """Detect remote/hybrid/onsite from text and location."""
    combined = f"{text} {location}".lower()
    if any(kw in combined for kw in ["remote", "work from home", "fully remote", "100% remote", "distributed"]):
        return "remote"
    if any(kw in combined for kw in ["hybrid", "partly remote", "flexible"]):
        return "hybrid"
    return "onsite"


def _detect_commitment(title: str, text: str) -> str:
    """Detect internship / full-time / part-time / contract."""
    combined = f"{title} {text}".lower()
    if any(kw in combined for kw in ["intern", "internship", "werkstudent", "working student", "practicum", "co-op"]):
        return "intern"
    if any(kw in combined for kw in ["part-time", "part time"]):
        return "part_time"
    if any(kw in combined for kw in ["contract", "freelance", "temporary"]):
        return "contract"
    return "full_time"


# ---------------------------------------------------------------------------
# Greenhouse provider
# ---------------------------------------------------------------------------

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


async def fetch_greenhouse(token: str, company: str, client: httpx.AsyncClient) -> list[dict]:
    """Fetch all jobs from a Greenhouse board. Returns normalized job dicts."""
    url = GREENHOUSE_API.format(token=token)
    try:
        resp = await client.get(url, params={"content": "true"})
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    jobs = []
    for item in data.get("jobs", []):
        # Extract content HTML
        content_html = item.get("content", "")
        description_text = html_to_text(content_html)

        # Location
        location_parts = []
        for loc in item.get("offices", []):
            if loc.get("name"):
                location_parts.append(loc["name"])
        location = ", ".join(location_parts) or item.get("location", {}).get("name", "")

        # Departments
        departments = [d.get("name", "") for d in item.get("departments", []) if d.get("name")]

        title = item.get("title", "")
        source_url = item.get("absolute_url", f"https://boards.greenhouse.io/{token}/jobs/{item.get('id', '')}")

        posted_at = item.get("updated_at") or item.get("created_at") or ""

        jobs.append({
            "id": f"gh-{token}-{item.get('id', '')}",
            "source": "greenhouse",
            "source_company_key": token,
            "title": title,
            "company": company,
            "location": location,
            "work_mode": _detect_work_mode(description_text, location),
            "commitment": _detect_commitment(title, description_text),
            "team": departments[0] if departments else "",
            "department": ", ".join(departments),
            "description": description_text[:8000],
            "description_html": content_html[:15000],
            "requirements": "",  # will be extracted below
            "responsibilities": "",  # will be extracted below
            "source_url": source_url,
            "posted_at": posted_at,
        })

    return jobs


# ---------------------------------------------------------------------------
# Lever provider
# ---------------------------------------------------------------------------

LEVER_API = "https://api.lever.co/v0/postings/{slug}"


async def fetch_lever(slug: str, company: str, client: httpx.AsyncClient) -> list[dict]:
    """Fetch all published postings from a Lever site. Returns normalized job dicts."""
    url = LEVER_API.format(slug=slug)
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    jobs = []
    for item in data:
        title = item.get("text", "")

        # Build description from Lever's lists structure
        description_parts = []
        description_html_parts = []
        requirements_text = ""
        responsibilities_text = ""

        for section in item.get("lists", []):
            heading = section.get("text", "")
            content = section.get("content", "")
            plain = html_to_text(content)
            description_parts.append(f"{heading}\n{plain}")
            description_html_parts.append(f"<h3>{heading}</h3>{content}")

            heading_lower = heading.lower()
            if any(kw in heading_lower for kw in ["requirement", "qualification", "what you", "who you", "must have", "need"]):
                requirements_text += plain + "\n"
            elif any(kw in heading_lower for kw in ["responsibilit", "what you'll do", "the role", "your role", "key duties"]):
                responsibilities_text += plain + "\n"

        # Additional description from the main text
        additional = html_to_text(item.get("descriptionPlain", "") or item.get("description", ""))
        if additional:
            description_parts.insert(0, additional)

        description_text = "\n\n".join(description_parts)

        # Categories
        cats = item.get("categories", {})
        location = cats.get("location", "") or ""
        commitment = cats.get("commitment", "") or ""
        team = cats.get("team", "") or ""
        department = cats.get("department", "") or ""

        source_url = item.get("hostedUrl") or item.get("applyUrl") or f"https://jobs.lever.co/{slug}/{item.get('id', '')}"
        posted_at = ""
        if item.get("createdAt"):
            try:
                posted_at = datetime.fromtimestamp(item["createdAt"] / 1000, tz=timezone.utc).isoformat()
            except Exception:
                pass

        jobs.append({
            "id": f"lv-{slug}-{item.get('id', '')}",
            "source": "lever",
            "source_company_key": slug,
            "title": title,
            "company": company,
            "location": location,
            "work_mode": _detect_work_mode(description_text, location),
            "commitment": _detect_commitment(title, f"{commitment} {description_text}"),
            "team": team,
            "department": department,
            "description": description_text[:8000],
            "description_html": "\n".join(description_html_parts)[:15000],
            "requirements": requirements_text.strip()[:3000],
            "responsibilities": responsibilities_text.strip()[:3000],
            "source_url": source_url,
            "posted_at": posted_at,
        })

    return jobs


# ---------------------------------------------------------------------------
# Search / filter
# ---------------------------------------------------------------------------

def _match_score(job: dict, query_terms: list[str]) -> float:
    """Score how well a job matches the search query (0-100)."""
    if not query_terms:
        return 50.0

    searchable = f"{job['title']} {job['company']} {job['location']} {job['team']} {job['department']} {job['commitment']}".lower()
    desc_lower = job.get("description", "").lower()

    score = 0.0
    matched = 0

    for term in query_terms:
        t = term.lower()
        if t in searchable:
            score += 20.0
            matched += 1
        elif t in desc_lower:
            score += 5.0
            matched += 1

    if matched == 0:
        return 0.0

    # Bonus for matching all terms
    if matched == len(query_terms):
        score += 15.0

    # Prefer internships/junior when query suggests it
    intern_terms = {"intern", "internship", "junior", "entry", "graduate", "werkstudent", "working student"}
    if intern_terms & set(query_terms):
        if job["commitment"] == "intern":
            score += 25.0

    return min(100.0, score)


def filter_and_rank(jobs: list[dict], query: str, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """Filter jobs by query and rank by relevance.

    Returns (page_of_jobs, total_matched) so callers can implement pagination.
    """
    terms = [t.strip().lower() for t in query.split() if t.strip()]

    scored = []
    for job in jobs:
        s = _match_score(job, terms)
        if s > 0:
            scored.append((s, job))

    scored.sort(key=lambda x: x[0], reverse=True)
    total = len(scored)
    page = [job for _, job in scored[offset:offset + limit]]
    return page, total


# ---------------------------------------------------------------------------
# Main fetch-all function
# ---------------------------------------------------------------------------

async def fetch_all_jobs() -> list[dict]:
    """Fetch jobs from all configured Greenhouse and Lever sources in parallel.

    Uses an in-memory cache (TTL 5 min) to avoid re-fetching on every search.
    All provider requests run concurrently via asyncio.gather for speed.
    """
    # Return cached results if still fresh
    if _job_cache["jobs"] and (time.time() - _job_cache["ts"]) < _CACHE_TTL:
        return _job_cache["jobs"]

    all_jobs: list[dict] = []

    async with httpx.AsyncClient(
        timeout=12.0,
        follow_redirects=True,
        headers={"Accept": "application/json"},
    ) as client:
        # Build all fetch tasks and run them concurrently
        tasks = []
        for board in GREENHOUSE_BOARDS:
            tasks.append(fetch_greenhouse(board["token"], board["company"], client))
        for site in LEVER_SITES:
            tasks.append(fetch_lever(site["slug"], site["company"], client))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_jobs.extend(result)
            # Exceptions are silently ignored (same as before)

    # Update cache
    _job_cache["jobs"] = all_jobs
    _job_cache["ts"] = time.time()

    return all_jobs


async def search_structured_jobs(query: str, limit: int = 20, offset: int = 0) -> dict:
    """Fetch from all providers, filter by query, return ranked results with pagination."""
    if not query or not query.strip():
        return {"results": [], "total": 0, "error": "Search query is required."}

    try:
        all_jobs = await fetch_all_jobs()
    except Exception as e:
        return {"results": [], "total": 0, "error": f"Could not fetch job listings: {str(e)}"}

    if not all_jobs:
        return {"results": [], "total": 0, "error": "No job listings available from configured sources. Please try again later."}

    results, total = filter_and_rank(all_jobs, query.strip(), limit, offset)

    if not results:
        return {
            "results": [],
            "total": total,
            "error": "No matching jobs found. Try broader terms like 'analyst' or 'intern'.",
        }

    return {"results": results, "total": total, "error": None}
