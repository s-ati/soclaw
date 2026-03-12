"""Job search using DuckDuckGo (free, no API key) with Google CSE fallback."""

import re
from urllib.parse import urlparse

import httpx
from .settings import settings

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# ---------------------------------------------------------------------------
# Skill / context extraction helpers
# ---------------------------------------------------------------------------
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


def _extract_company(title: str, snippet: str, source: str) -> str:
    """Try to extract a company name from the search result."""
    boards = ["indeed", "linkedin", "glassdoor", "ziprecruiter",
              "monster", "dice", "angel", "wellfound", "google",
              "duckduckgo", "bing"]
    for sep in [" at ", " - ", " | ", " — ", " – "]:
        if sep in title:
            parts = title.split(sep)
            if len(parts) >= 2:
                candidate = parts[-1].strip()
                if not any(b in candidate.lower() for b in boards):
                    return candidate[:100]
    # Fallback: domain name
    domain = source.replace("www.", "").split(".")[0] if source else "Unknown"
    return domain.title()


def _extract_skills(text: str) -> list[dict]:
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
    for pattern in [r"\s*[\|–—-]\s*(Indeed|LinkedIn|Glassdoor|ZipRecruiter|Monster|Dice|Google|DuckDuckGo).*$",
                    r"\s*-\s*job posting.*$"]:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    return title.strip()[:200]


# ---------------------------------------------------------------------------
# Normalize a search result (works for both DDG and Google)
# ---------------------------------------------------------------------------
def _normalize(title: str, snippet: str, url: str, source: str) -> dict:
    title = _clean_title(title)
    combined = f"{title} {snippet}"
    return {
        "title": title,
        "company": _extract_company(title, snippet, source),
        "location_policy": _detect_location_policy(combined),
        "required_skills": _extract_skills(combined),
        "nice_to_have_skills": [],
        "context_keywords": _extract_context_keywords(combined),
        "snippet": snippet.strip(),
        "url": url,
        "source": source,
    }


# ---------------------------------------------------------------------------
# DuckDuckGo search (free, no API key)
# ---------------------------------------------------------------------------
async def _search_ddg(query: str, num: int = 10) -> dict:
    """Search using duckduckgo-search library (runs sync in thread)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _do_search():
        from duckduckgo_search import DDGS
        results = DDGS().text(f"{query} job", max_results=num)
        return results

    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            raw = await loop.run_in_executor(pool, _do_search)

        if not raw:
            return {"results": [], "error": "No results found. Try a different search term."}

        results = []
        for item in raw:
            url = item.get("href", "")
            source = urlparse(url).netloc if url else ""
            results.append(_normalize(
                title=item.get("title", "Unknown Position"),
                snippet=item.get("body", ""),
                url=url,
                source=source,
            ))
        return {"results": results, "error": None}

    except Exception as e:
        err = str(e)
        if "ConnectError" in err or "ConnectionError" in err:
            return {"results": [], "error": "Could not connect to search service. Check network."}
        if "RatelimitE" in err:
            return {"results": [], "error": "Search rate limit reached. Please wait a moment and try again."}
        return {"results": [], "error": f"Search failed: {err}"}


# ---------------------------------------------------------------------------
# Google Custom Search (optional fallback)
# ---------------------------------------------------------------------------
async def _search_google(query: str, num: int = 10) -> dict:
    params = {
        "key": settings.google_search_api_key,
        "cx": settings.google_cse_id,
        "q": f"{query} job",
        "num": min(num, 10),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(GOOGLE_SEARCH_URL, params=params)

        error_detail = None
        try:
            data = resp.json()
        except Exception:
            data = None
            if resp.status_code == 403:
                error_detail = "Network blocked access to Google APIs."

        if resp.status_code != 200:
            if error_detail:
                return {"results": [], "error": error_detail}
            if data and "error" in data:
                msg = data["error"].get("message", "Unknown error")
                code = data["error"].get("code", resp.status_code)
                return {"results": [], "error": f"Google API error {code}: {msg}"}
            return {"results": [], "error": f"Google API returned status {resp.status_code}."}

        if data is None:
            return {"results": [], "error": "Invalid response from Google API."}

        items = data.get("items", [])
        results = []
        for item in items:
            results.append(_normalize(
                title=item.get("title", "Unknown Position"),
                snippet=item.get("snippet", ""),
                url=item.get("link", ""),
                source=item.get("displayLink", ""),
            ))
        return {"results": results, "error": None}

    except httpx.TimeoutException:
        return {"results": [], "error": "Search request timed out."}
    except httpx.ConnectError:
        return {"results": [], "error": "Could not connect to Google APIs."}
    except Exception as e:
        return {"results": [], "error": f"Google search failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Main entry point: tries DDG first, falls back to Google if configured
# ---------------------------------------------------------------------------
async def search_jobs(query: str, num: int = 10) -> dict:
    """Search for jobs. Uses DuckDuckGo (free) first, Google CSE as fallback."""
    if not query or not query.strip():
        return {"results": [], "error": "Search query is required."}

    # Try DuckDuckGo first (free, no key needed)
    result = await _search_ddg(query.strip(), num)

    # If DDG failed and Google is configured, try Google
    if result.get("error") and settings.google_search_api_key and settings.google_cse_id:
        google_result = await _search_google(query.strip(), num)
        if not google_result.get("error"):
            return google_result
        # Both failed — return DDG error (more likely a network issue)

    return result
