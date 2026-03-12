"""Job search — delegates to structured Greenhouse/Lever providers.

This module is the public API used by main.py. It wraps the provider
layer so the rest of the app doesn't need to know about individual sources.
"""

from .job_providers import search_structured_jobs


async def search_jobs(query: str, num: int = 12) -> dict:
    """Search configured Greenhouse + Lever sources for matching jobs."""
    return await search_structured_jobs(query, limit=num)
