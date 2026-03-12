"""Job search using DuckDuckGo (free, no API key) with Google CSE fallback.

Biases search towards ATS platforms (Greenhouse, Lever, Ashby, etc.) that
serve real HTML job postings extractable without JS rendering.
"""

import re
from urllib.parse import urlparse

import httpx
from .settings import settings

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

# ATS / careers platforms that serve real server-side HTML (extractable)
EXTRACTABLE_DOMAINS = {
    "greenhouse.io", "boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "ashbyhq.com", "jobs.ashbyhq.com",
    "workable.com", "apply.workable.com",
    "smartrecruiters.com", "jobs.smartrecruiters.com",
    "bamboohr.com",
    "recruitee.com",
    "breezy.hr",
    "join.com",
    "personio.de", "jobs.personio.de",
    "teamtailor.com",
    "myworkdayjobs.com",
    "icims.com",
}

# Domains that block server-side fetch (JS-rendered SPAs or anti-bot)
BLOCKED_DOMAINS = {
    "indeed.com", "www.indeed.com", "de.indeed.com",
    "linkedin.com", "www.linkedin.com",
    "glassdoor.com", "www.glassdoor.com",
    "ziprecruiter.com", "www.ziprecruiter.com",
    "monster.com", "www.monster.com",
    "dice.com", "www.dice.com",
}

# Pure search/aggregator domains (never useful)
AGGREGATOR_DOMAINS = {
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
    "jooble.org", "talent.com", "careerbuilder.com",
}

# ---------------------------------------------------------------------------
# Search-result quality filtering
# ---------------------------------------------------------------------------
AGGREGATOR_PATTERNS = [
    r"\d{1,3},?\d{3}\+?\s+(?:jobs?|positions?|openings?|results?)",
    r"\d{2,}\s+(?:jobs?|positions?|openings?)\s+(?:available|found|near|in)",
    r"search\s+(?:results?|jobs?)\s+(?:for|in)",
    r"browse\s+(?:all|our)\s+(?:jobs?|openings?|positions?)",
    r"top\s+\d+\s+(?:jobs?|companies)",
    r"apply\s+to\s+\d+\s+",
    r"page\s+\d+\s+of\s+\d+",
]


def _get_domain(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "").lower()


def _is_extractable_domain(url: str) -> bool:
    domain = _get_domain(url)
    # Check exact match or subdomain match
    return any(domain == d or domain.endswith("." + d) for d in EXTRACTABLE_DOMAINS)


def _is_blocked_domain(url: str) -> bool:
    domain = _get_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in BLOCKED_DOMAINS)


def _is_careers_page(url: str) -> bool:
    """Check if URL looks like a direct company careers page."""
    path = urlparse(url).path.lower()
    domain = _get_domain(url)
    # /careers/job-title, /jobs/12345, etc.
    if re.search(r"/(?:careers?|jobs?|positions?|openings?)/[a-z0-9]", path):
        return True
    # Company domains with /career or /job paths
    if ("careers" in domain or "jobs" in domain) and path != "/":
        return True
    return False


def _is_aggregator_text(title: str, snippet: str) -> bool:
    combined = f"{title} {snippet}".lower()
    return any(re.search(pat, combined, re.IGNORECASE) for pat in AGGREGATOR_PATTERNS)


def _result_quality_score(title: str, snippet: str, url: str) -> int:
    """Score how likely this result leads to an extractable single job posting."""
    score = 40  # baseline
    domain = _get_domain(url)
    combined = f"{title} {snippet}".lower()
    path = urlparse(url).path.lower()

    # === Strong signals ===

    # Extractable ATS domains get huge boost
    if _is_extractable_domain(url):
        score += 40

    # Direct company careers pages
    elif _is_careers_page(url):
        score += 25

    # Blocked domains (can't extract) get heavy penalty
    elif _is_blocked_domain(url):
        score -= 30

    # Pure aggregators: exclude
    elif domain in AGGREGATOR_DOMAINS:
        score -= 60

    # === Medium signals ===

    # URL path suggests individual listing (has ID or slug)
    if re.search(r"/(?:jobs?|careers?|positions?)/\d+", path):
        score += 15
    elif re.search(r"/(?:jobs?|careers?|positions?)/[a-z][\w-]+$", path):
        score += 10

    # Title contains job role terms
    job_terms = ["intern", "analyst", "engineer", "developer", "manager", "designer",
                 "coordinator", "specialist", "associate", "consultant", "assistant",
                 "director", "lead", "senior", "junior", "architect", "scientist"]
    if any(t in combined for t in job_terms):
        score += 10

    # === Negative signals ===

    # Aggregator text patterns
    if _is_aggregator_text(title, snippet):
        score -= 35

    # Very short snippet
    if len(snippet) < 30:
        score -= 10

    return max(0, min(100, score))


def _clean_title(title: str) -> str:
    for pattern in [r"\s*[\|–—-]\s*(Indeed|LinkedIn|Glassdoor|ZipRecruiter|Monster|Dice|Google|DuckDuckGo|Lever|Greenhouse).*$",
                    r"\s*-\s*job posting.*$"]:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    return title.strip()[:200]


def _extract_company(title: str, source: str) -> str:
    boards = ["indeed", "linkedin", "glassdoor", "ziprecruiter",
              "monster", "dice", "angel", "wellfound", "google",
              "duckduckgo", "bing", "greenhouse", "lever"]
    for sep in [" at ", " - ", " | ", " — ", " – "]:
        if sep in title:
            parts = title.split(sep)
            if len(parts) >= 2:
                candidate = parts[-1].strip()
                if not any(b in candidate.lower() for b in boards):
                    return candidate[:100]
    domain = source.replace("www.", "").split(".")[0] if source else "Unknown"
    return domain.title()


def _normalize(title: str, snippet: str, url: str, source: str) -> dict:
    title = _clean_title(title)
    quality = _result_quality_score(title, snippet, url)
    is_extractable = _is_extractable_domain(url) or _is_careers_page(url)
    is_blocked = _is_blocked_domain(url)
    return {
        "title": title,
        "company": _extract_company(title, source),
        "snippet": snippet.strip(),
        "url": url,
        "source": source,
        "quality_score": quality,
        "is_extractable": is_extractable,
        "is_blocked": is_blocked,
        "is_aggregator": _is_aggregator_text(title, snippet),
    }


def _filter_and_rank(results: list[dict]) -> list[dict]:
    """Filter out junk and rank by extractability."""
    # Remove pure aggregators and very low quality
    filtered = [r for r in results if r["quality_score"] >= 15]
    # Sort: extractable first, then by quality score
    filtered.sort(key=lambda r: (r["is_extractable"], r["quality_score"]), reverse=True)
    return filtered


# ---------------------------------------------------------------------------
# DuckDuckGo search — biased towards ATS platforms
# ---------------------------------------------------------------------------
async def _search_ddg(query: str, num: int = 10) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _do_search():
        from duckduckgo_search import DDGS
        ddgs = DDGS()

        # Strategy: run two searches — one ATS-biased, one general — merge results
        ats_query = f"{query} site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:workable.com"
        general_query = f"{query} job careers apply"

        ats_results = []
        general_results = []

        try:
            ats_results = ddgs.text(ats_query, max_results=10) or []
        except Exception:
            pass

        try:
            general_results = ddgs.text(general_query, max_results=15) or []
        except Exception:
            pass

        # Merge: ATS results first, then general, deduplicate by URL
        seen_urls = set()
        merged = []
        for item in ats_results + general_results:
            url = item.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                merged.append(item)

        return merged

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

        if not filtered:
            return {"results": [], "error": "No extractable job postings found. Try different keywords."}

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
        "q": f"{query} job careers apply site:greenhouse.io OR site:lever.co",
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
