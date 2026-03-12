"""Job search using DuckDuckGo (free, no API key) with Google CSE fallback.

Search results are treated as discovery leads, not final job objects.
Real job data is extracted from source URLs via job_extractor.py.
"""

import re
from urllib.parse import urlparse

import httpx
from .settings import settings

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# ---------------------------------------------------------------------------
# Search-result quality filtering
# ---------------------------------------------------------------------------
AGGREGATOR_PATTERNS = [
    r"\d{1,3},?\d{3}\+?\s+(?:jobs?|positions?|openings?|results?)",
    r"\d{2,}\s+(?:jobs?|positions?|openings?)\s+(?:available|found|near|in)",
    r"search\s+(?:results?|jobs?)\s+(?:for|in)",
    r"browse\s+(?:all|our)\s+(?:jobs?|openings?|positions?)",
    r"job\s+(?:board|listing|search|alert)",
    r"top\s+\d+\s+(?:jobs?|companies)",
    r"apply\s+to\s+\d+\s+",
    r"page\s+\d+\s+of\s+\d+",
]

AGGREGATOR_DOMAINS = {"google.com", "bing.com", "duckduckgo.com", "yahoo.com"}

# Domains that host individual, extractable job postings (ATS platforms + career pages)
HIGH_EXTRACTABILITY_DOMAINS = {
    "greenhouse.io", "lever.co", "ashbyhq.com", "boards.greenhouse.io",
    "jobs.lever.co", "jobs.ashbyhq.com", "workable.com", "apply.workable.com",
    "bamboohr.com", "recruitee.com", "breezy.hr", "dover.com",
    "jobs.smartrecruiters.com", "smartrecruiters.com",
}

# Generic job boards — sometimes link to individual postings, sometimes listing pages
JOB_BOARD_DOMAINS = {
    "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "dice.com", "wellfound.com", "lever.co", "greenhouse.io",
    "workday.com", "smartrecruiters.com",
}

# Domains that almost never contain a single extractable job posting
LISTING_PAGE_DOMAINS = {
    "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
    "simplyhired.com", "careerbuilder.com", "jooble.org", "neuvoo.com",
    "talent.com", "adzuna.com", "reed.co.uk", "jobrapido.com",
}


def _is_aggregator_result(title: str, snippet: str, url: str) -> bool:
    combined = f"{title} {snippet}".lower()
    for pat in AGGREGATOR_PATTERNS:
        if re.search(pat, combined, re.IGNORECASE):
            return True
    domain = urlparse(url).netloc.replace("www.", "")
    return domain in AGGREGATOR_DOMAINS


def _url_looks_like_single_posting(url: str) -> bool:
    """Heuristic: does this URL look like a single job posting rather than a listing page?"""
    path = urlparse(url).path.lower()
    # ATS-style paths with a job ID or slug
    if re.search(r"/(?:jobs?|careers?|positions?|openings?|roles?)/[\w-]{4,}", path):
        return True
    # Greenhouse / Lever / Ashby canonical paths
    if re.search(r"/(?:boards|jobs)\.[a-z]+\.(?:io|co|com)/\w+/jobs/\d+", url.lower()):
        return True
    # Numeric ID at end of path (common in ATS URLs)
    if re.search(r"/\d{4,}/?$", path):
        return True
    return False


def _result_quality_score(title: str, snippet: str, url: str) -> int:
    """Score a search result for likelihood of being a single, extractable job posting.

    High scores (70+) = very likely a real posting on an ATS or company careers page.
    Low scores (<35)  = listing/aggregator page that won't extract well.
    """
    score = 40
    combined = f"{title} {snippet}".lower()
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    path = parsed.path.lower()

    # --- Strong boost: ATS / high-extractability domains ---
    if any(ats in domain for ats in HIGH_EXTRACTABILITY_DOMAINS):
        score += 30
    # --- Moderate boost: known job boards (individual postings sometimes) ---
    elif any(jb in domain for jb in JOB_BOARD_DOMAINS):
        score += 5
    # --- Company careers page (domain contains /careers or /jobs path) ---
    elif re.search(r"/(?:careers?|jobs?)/", path):
        score += 15

    # URL structure: single-posting patterns
    if _url_looks_like_single_posting(url):
        score += 20
    # URL structure: listing/search page patterns
    elif re.search(r"/(?:search|results|browse|category|tag|location)", path):
        score -= 20
    elif re.search(r"[?&](?:q=|query=|search=|page=|start=)", url.lower()):
        score -= 15

    # Job title keywords in title/snippet
    job_terms = ["intern", "analyst", "engineer", "developer", "manager", "designer",
                 "coordinator", "specialist", "associate", "consultant", "assistant",
                 "director", "scientist", "architect"]
    if any(t in combined for t in job_terms):
        score += 10

    # Aggregator pattern match (strong penalty)
    if _is_aggregator_result(title, snippet, url):
        score -= 40

    # Listing-page domains (almost never extractable as single postings)
    if any(lp in domain for lp in LISTING_PAGE_DOMAINS):
        score -= 25

    # Short snippet = low information = probably not a real posting page
    if len(snippet) < 30:
        score -= 10

    return max(0, min(100, score))


def _clean_title(title: str) -> str:
    for pattern in [r"\s*[\|–—-]\s*(Indeed|LinkedIn|Glassdoor|ZipRecruiter|Monster|Dice|Google|DuckDuckGo).*$",
                    r"\s*-\s*job posting.*$"]:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    return title.strip()[:200]


def _extract_company(title: str, source: str) -> str:
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
    domain = source.replace("www.", "").split(".")[0] if source else "Unknown"
    return domain.title()


def _extractability(quality_score: int, url: str) -> str:
    """Classify how likely we are to extract a usable job description from this URL."""
    domain = urlparse(url).netloc.replace("www.", "")
    if any(ats in domain for ats in HIGH_EXTRACTABILITY_DOMAINS):
        if quality_score >= 50:
            return "high"
        return "medium"
    if quality_score >= 65:
        return "high"
    if quality_score >= 40:
        return "medium"
    return "low"


def _normalize(title: str, snippet: str, url: str, source: str) -> dict:
    title = _clean_title(title)
    quality = _result_quality_score(title, snippet, url)
    ext = _extractability(quality, url)
    return {
        "title": title,
        "company": _extract_company(title, source),
        "snippet": snippet.strip(),
        "url": url,
        "source": source,
        "quality_score": quality,
        "extractability": ext,
        "is_aggregator": _is_aggregator_result(title, snippet, url),
    }


# Minimum quality threshold — results below this are dropped entirely
_MIN_QUALITY = 30


def _filter_and_rank(results: list[dict]) -> list[dict]:
    # Drop results that are almost certainly not single job postings
    filtered = [r for r in results if r["quality_score"] >= _MIN_QUALITY]
    # Sort: high-extractability first, then by quality score
    tier_order = {"high": 0, "medium": 1, "low": 2}
    filtered.sort(key=lambda r: (tier_order.get(r.get("extractability"), 2), -r["quality_score"]))
    return filtered


# ---------------------------------------------------------------------------
# DuckDuckGo search
# ---------------------------------------------------------------------------
async def _search_ddg(query: str, num: int = 10) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _do_search():
        from duckduckgo_search import DDGS
        # Broaden fetch count so we have room to filter aggressively
        return DDGS().text(f"{query} job posting", max_results=min(num * 3, 30))

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

        filtered = _filter_and_rank(results)[:num]
        return {"results": filtered, "error": None}

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
        "q": f"{query} job posting",
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

        filtered = _filter_and_rank(results)[:num]
        return {"results": filtered, "error": None}

    except httpx.TimeoutException:
        return {"results": [], "error": "Search request timed out."}
    except httpx.ConnectError:
        return {"results": [], "error": "Could not connect to Google APIs."}
    except Exception as e:
        return {"results": [], "error": f"Google search failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def search_jobs(query: str, num: int = 10) -> dict:
    if not query or not query.strip():
        return {"results": [], "error": "Search query is required."}

    result = await _search_ddg(query.strip(), num)

    if result.get("error") and settings.google_search_api_key and settings.google_cse_id:
        google_result = await _search_google(query.strip(), num)
        if not google_result.get("error"):
            return google_result

    return result
