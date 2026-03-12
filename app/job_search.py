"""Curated job search: only returns results from supported ATS platforms
and company careers pages where single-posting extraction is reliable.

Uses DuckDuckGo for discovery with site:-scoped queries, then applies
strict allowlist + URL-pattern filtering so every result shown to the
user is assessment-ready.
"""

import re
from urllib.parse import urlparse

import httpx
from .settings import settings

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# ---------------------------------------------------------------------------
# Supported sources — the only domains we trust for extraction
# ---------------------------------------------------------------------------

# ATS platforms that always host single-posting pages
SUPPORTED_ATS_DOMAINS = {
    "greenhouse.io",
    "boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "workable.com",
    "apply.workable.com",
    "bamboohr.com",
    "recruitee.com",
    "breezy.hr",
    "dover.com",
    "jobs.smartrecruiters.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "icims.com",
}

# Domains that are never single-posting pages — hard blocklist
BLOCKED_DOMAINS = {
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
    "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
    "simplyhired.com", "careerbuilder.com", "jooble.org", "neuvoo.com",
    "talent.com", "adzuna.com", "reed.co.uk", "jobrapido.com",
    "linkedin.com", "facebook.com", "twitter.com", "reddit.com",
    "wikipedia.org", "youtube.com", "tiktok.com",
    "salary.com", "payscale.com", "comparably.com",
}

# URL path patterns that indicate a listing/search/collection page (not a posting)
LISTING_PATH_PATTERNS = re.compile(
    r"/(?:search|results|browse|category|categories|tag|tags|"
    r"all-jobs|all-positions|locations?|departments?|teams?|"
    r"employment-in|jobs-in|careers-in|"
    r"index\.html?|sitemap)"
    r"(?:/|$|\?)",
    re.IGNORECASE,
)

LISTING_QUERY_PATTERNS = re.compile(
    r"[?&](?:q=|query=|search=|page=\d|start=\d|offset=|category=|department=)",
    re.IGNORECASE,
)

# Title/snippet patterns that indicate aggregation, not a single posting
COLLECTION_TITLE_PATTERNS = [
    re.compile(r"\d{1,3}[,.]?\d{3}\+?\s+(?:jobs?|positions?|openings?|results?)", re.I),
    re.compile(r"\d{2,}\s+(?:jobs?|positions?|openings?)\s+(?:available|found|near|in)", re.I),
    re.compile(r"(?:search|browse|find|view|explore)\s+(?:all\s+)?(?:jobs?|openings?|positions?|careers?)", re.I),
    re.compile(r"(?:all|top|best)\s+\d*\s*(?:jobs?|positions?|openings?|roles?)", re.I),
    re.compile(r"job\s+(?:board|listing|search|alert|results)", re.I),
    re.compile(r"page\s+\d+\s+of\s+\d+", re.I),
    re.compile(r"apply\s+to\s+\d+\s+", re.I),
    re.compile(r"(?:jobs?|openings?|positions?)\s+(?:in|near|at)\s+.{2,30}$", re.I),
]

# Board names to strip from titles
BOARD_NAMES = [
    "Indeed", "LinkedIn", "Glassdoor", "ZipRecruiter", "Monster",
    "Dice", "Google", "DuckDuckGo", "SimplyHired",
]


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------

def _is_supported_ats(domain: str) -> bool:
    """Check if the domain is a known ATS platform."""
    d = domain.lower().replace("www.", "")
    return any(ats in d for ats in SUPPORTED_ATS_DOMAINS)


def _is_blocked_domain(domain: str) -> bool:
    d = domain.lower().replace("www.", "")
    return any(blocked in d for blocked in BLOCKED_DOMAINS)


def _is_company_careers_posting(url: str) -> bool:
    """Heuristic: is this a direct company careers page with a single-posting URL?

    Accepted patterns:
      /careers/some-job-title
      /jobs/12345
      /job/senior-engineer-12345
      /positions/backend-developer
      /en/careers/openings/data-analyst
    """
    parsed = urlparse(url)
    path = parsed.path.lower()

    # Must have a careers/jobs path segment
    if not re.search(r"/(?:careers?|jobs?|positions?|openings?|roles?|vacancies)/", path):
        return False

    # Must have a specific posting slug or ID after the segment
    if re.search(r"/(?:careers?|jobs?|positions?|openings?|roles?|vacancies)/[\w][\w-]{3,}", path):
        return True

    # Numeric ID at end of path
    if re.search(r"/\d{4,}/?$", path):
        return True

    return False


def _looks_like_listing_page(title: str, snippet: str, url: str) -> bool:
    """Returns True if this looks like a collection/listing page."""
    combined = f"{title} {snippet}"

    # Check title/snippet patterns
    for pat in COLLECTION_TITLE_PATTERNS:
        if pat.search(combined):
            return True

    parsed = urlparse(url)
    path = parsed.path.lower()

    # Check URL path patterns
    if LISTING_PATH_PATTERNS.search(path):
        return True

    # Check query-string patterns
    if LISTING_QUERY_PATTERNS.search(url):
        return True

    # Bare /careers or /jobs with no specific posting
    if re.match(r"^/(?:careers?|jobs?)/?$", path):
        return True

    return False


def _is_supported_result(title: str, snippet: str, url: str) -> bool:
    """Gate: does this result come from a supported source AND look like a single posting?"""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")

    # Hard blocklist
    if _is_blocked_domain(domain):
        return False

    # Collection/listing pages are never supported
    if _looks_like_listing_page(title, snippet, url):
        return False

    # ATS domains are always supported (they host single postings by design)
    if _is_supported_ats(domain):
        return True

    # Company careers pages with a posting-like URL
    if _is_company_careers_posting(url):
        return True

    return False


# ---------------------------------------------------------------------------
# Result normalization
# ---------------------------------------------------------------------------

def _clean_title(title: str) -> str:
    for board in BOARD_NAMES:
        title = re.sub(rf"\s*[\|–—-]\s*{board}.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*job posting.*$", "", title, flags=re.IGNORECASE)
    return title.strip()[:200]


def _extract_company(title: str, source: str) -> str:
    skip = {b.lower() for b in BOARD_NAMES} | {"angel", "wellfound"}
    for sep in [" at ", " - ", " | ", "\u2014", "\u2013"]:
        if sep in title:
            parts = title.split(sep)
            if len(parts) >= 2:
                candidate = parts[-1].strip()
                if not any(b in candidate.lower() for b in skip):
                    return candidate[:100]
    domain = source.replace("www.", "").split(".")[0] if source else "Unknown"
    return domain.title()


def _source_label(domain: str) -> str:
    """Friendly label for the source domain."""
    d = domain.lower().replace("www.", "")
    if "greenhouse" in d:
        return "Greenhouse"
    if "lever" in d:
        return "Lever"
    if "ashby" in d:
        return "Ashby"
    if "workable" in d:
        return "Workable"
    if "smartrecruiters" in d:
        return "SmartRecruiters"
    if "bamboohr" in d:
        return "BambooHR"
    if "recruitee" in d:
        return "Recruitee"
    if "breezy" in d:
        return "Breezy"
    if "dover" in d:
        return "Dover"
    if "workday" in d or "myworkdayjobs" in d:
        return "Workday"
    if "icims" in d:
        return "iCIMS"
    # Company careers page — use cleaned domain
    clean = d.split(".")[0]
    return f"{clean.title()} Careers"


def _normalize(title: str, snippet: str, url: str, source: str) -> dict:
    title = _clean_title(title)
    domain = urlparse(url).netloc.lower().replace("www.", "")
    is_ats = _is_supported_ats(domain)
    return {
        "title": title,
        "company": _extract_company(title, source),
        "snippet": snippet.strip(),
        "url": url,
        "source": _source_label(source),
        "source_type": "ats" if is_ats else "careers",
        "supported": True,  # only supported results reach this point
    }


def _rank(results: list[dict]) -> list[dict]:
    """Rank supported results: ATS first, then careers pages."""
    results.sort(key=lambda r: (0 if r.get("source_type") == "ats" else 1))
    return results


# ---------------------------------------------------------------------------
# DuckDuckGo search
# ---------------------------------------------------------------------------
async def _search_ddg(query: str, num: int = 8) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _do_search():
        from duckduckgo_search import DDGS
        # Single broad query — DDG rate-limits parallel requests
        return DDGS().text(f"{query} job posting", max_results=min(num * 5, 40))

    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            all_raw = await loop.run_in_executor(pool, _do_search)

        if not all_raw:
            return {"results": [], "error": "No results found. Try a different search term."}

        # Deduplicate by URL
        seen_urls = set()
        results = []
        for item in all_raw:
            url = item.get("href", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            source = urlparse(url).netloc if url else ""
            title = item.get("title", "Unknown Position")
            snippet = item.get("body", "")

            # Only include supported results
            if not _is_supported_result(title, snippet, url):
                continue

            results.append(_normalize(
                title=title,
                snippet=snippet,
                url=url,
                source=source,
            ))

        ranked = _rank(results)[:num]

        if not ranked:
            return {
                "results": [],
                "error": "No assessment-ready postings found for this search. Try adding a company name or searching for a more specific role title.",
            }

        return {"results": ranked, "error": None}

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
async def _search_google(query: str, num: int = 8) -> dict:
    params = {
        "key": settings.google_search_api_key,
        "cx": settings.google_cse_id,
        "q": f"{query} job posting site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:workable.com",
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
            url = item.get("link", "")
            title = item.get("title", "Unknown Position")
            snippet = item.get("snippet", "")
            source = item.get("displayLink", "")

            if not _is_supported_result(title, snippet, url):
                continue

            results.append(_normalize(
                title=title,
                snippet=snippet,
                url=url,
                source=source,
            ))

        ranked = _rank(results)[:num]
        if not ranked:
            return {
                "results": [],
                "error": "No assessment-ready postings found for this search.",
            }
        return {"results": ranked, "error": None}

    except httpx.TimeoutException:
        return {"results": [], "error": "Search request timed out."}
    except httpx.ConnectError:
        return {"results": [], "error": "Could not connect to Google APIs."}
    except Exception as e:
        return {"results": [], "error": f"Google search failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def search_jobs(query: str, num: int = 8) -> dict:
    if not query or not query.strip():
        return {"results": [], "error": "Search query is required."}

    result = await _search_ddg(query.strip(), num)

    if result.get("error") and settings.google_search_api_key and settings.google_cse_id:
        google_result = await _search_google(query.strip(), num)
        if not google_result.get("error"):
            return google_result

    return result
