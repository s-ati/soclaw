"""Google Custom Search API client for live job search."""

import httpx
import re
from urllib.parse import urlencode
from .settings import settings

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# Default skill mappings for search-result jobs
SKILL_KEYWORD_MAP = {
    "excel": ["excel", "spreadsheet", "vba"],
    "powerpoint": ["powerpoint", "presentation", "slides"],
    "sql": ["sql", "database", "mysql", "postgres"],
    "analysis": ["analysis", "analytical", "data analysis", "research"],
    "financial_modeling": ["financial model", "valuation", "dcf", "modeling"],
    "python": ["python", "pandas", "numpy", "programming"],
}

CONTEXT_KEYWORD_MAP = [
    "corporate", "stakeholder", "matrix", "startup", "regulated",
    "cross-functional", "creative", "tech", "consulting",
]

LOCATION_KEYWORDS = {
    "remote": ["remote", "work from home", "wfh", "anywhere"],
    "hybrid": ["hybrid", "flexible"],
    "onsite": ["onsite", "on-site", "in-office", "office-based"],
}


def _detect_location_policy(text: str) -> str:
    t = text.lower()
    for policy, keywords in LOCATION_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return policy
    return "onsite"


def _extract_company(title: str, snippet: str, display_link: str) -> str:
    """Try to extract a company name from the search result."""
    # Common job board patterns: "Title at Company" or "Title - Company"
    for sep in [" at ", " - ", " | ", " — ", " – "]:
        if sep in title:
            parts = title.split(sep)
            if len(parts) >= 2:
                candidate = parts[-1].strip()
                # Filter out job board names
                boards = ["indeed", "linkedin", "glassdoor", "ziprecruiter",
                          "monster", "dice", "angel", "wellfound", "google"]
                if not any(b in candidate.lower() for b in boards):
                    return candidate[:100]

    # Fallback: use the display link domain
    domain = display_link.replace("www.", "").split(".")[0]
    return domain.title()


def _extract_skills(text: str) -> list[dict]:
    """Extract skills from job description text."""
    t = text.lower()
    skills = []
    for canonical, variants in SKILL_KEYWORD_MAP.items():
        if any(v in t for v in variants):
            skills.append({"name": canonical, "tier": "must", "weight": 2})
    return skills


def _extract_context_keywords(text: str) -> list[str]:
    t = text.lower()
    return [kw for kw in CONTEXT_KEYWORD_MAP if kw in t]


def _clean_title(title: str) -> str:
    """Remove job board suffixes and clean up the title."""
    # Remove common suffixes like "| Indeed.com", "- LinkedIn", etc.
    for pattern in [r"\s*[\|–—-]\s*(Indeed|LinkedIn|Glassdoor|ZipRecruiter|Monster|Dice|Google).*$",
                    r"\s*-\s*job posting.*$"]:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    return title.strip()[:200]


def normalize_result(item: dict) -> dict:
    """Normalize a Google Custom Search result into our job card format."""
    raw_title = item.get("title", "Unknown Position")
    snippet = item.get("snippet", "")
    link = item.get("link", "")
    display_link = item.get("displayLink", "")

    title = _clean_title(raw_title)
    company = _extract_company(raw_title, snippet, display_link)
    combined_text = f"{title} {snippet}"

    location_policy = _detect_location_policy(combined_text)
    required_skills = _extract_skills(combined_text)
    context_keywords = _extract_context_keywords(combined_text)

    return {
        "title": title,
        "company": company,
        "location_policy": location_policy,
        "required_skills": required_skills,
        "nice_to_have_skills": [],
        "context_keywords": context_keywords,
        "snippet": snippet.strip(),
        "url": link,
        "source": display_link,
    }


async def search_jobs(query: str, num: int = 10) -> dict:
    """Search for jobs using Google Custom Search API.

    Returns {"results": [...], "error": None} on success,
    or {"results": [], "error": "message"} on failure.
    """
    if not settings.google_search_api_key or not settings.google_cse_id:
        return {"results": [], "error": "Google Search API not configured."}

    if not query or not query.strip():
        return {"results": [], "error": "Search query is required."}

    # Append "job" to query to bias towards job listings
    search_query = f"{query.strip()} job"

    params = {
        "key": settings.google_search_api_key,
        "cx": settings.google_cse_id,
        "q": search_query,
        "num": min(num, 10),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(GOOGLE_SEARCH_URL, params=params)

        # Try to parse as JSON first (Google API errors are JSON)
        error_detail = None
        try:
            data = resp.json()
        except Exception:
            data = None
            # HTML error page — likely a network/proxy block, not a Google API error
            if resp.status_code == 403:
                error_detail = ("Network blocked access to Google APIs. "
                                "This may be a firewall or proxy restriction.")

        if resp.status_code != 200:
            if error_detail:
                return {"results": [], "error": error_detail}
            # Structured Google API error
            if data and "error" in data:
                msg = data["error"].get("message", "Unknown error")
                code = data["error"].get("code", resp.status_code)
                return {"results": [], "error": f"Google API error {code}: {msg}"}
            return {"results": [], "error": f"Google API returned status {resp.status_code}."}

        if data is None:
            return {"results": [], "error": "Invalid response from Google API."}

        items = data.get("items", [])
        results = [normalize_result(item) for item in items]
        return {"results": results, "error": None}

    except httpx.TimeoutException:
        return {"results": [], "error": "Search request timed out. Please try again."}
    except httpx.ConnectError:
        return {"results": [], "error": "Could not connect to Google APIs. Check your network connection."}
    except Exception as e:
        return {"results": [], "error": f"Search failed: {str(e)}"}
