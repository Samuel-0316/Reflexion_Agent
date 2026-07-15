"""
Web search wrapper — backed by Tavily, restricted to Indian retailers.
"""

from __future__ import annotations

import logging
from tavily import TavilyClient
import re

from config import TAVILY_API_KEY, MAX_SEARCH_RESULTS

logger = logging.getLogger("search")

INDIAN_RETAILERS = [
    "amazon.in",
    "flipkart.com",
    "croma.com",
    "reliancedigital.in",
    "vijaysales.com",
    "tatacliq.com",
    "myntra.com",
    "snapdeal.com",
    "shopclues.com",
    "pricebefore.com",
    "smartprix.com",
    "mysmartprice.com",
    "91mobiles.com",
    "boat-lifestyle.com",
    "mi.com",
    "oneplus.in",
    "samsung.com",
    "apple.com",
]

def _sanitize_query(query: str) -> str:
    """Strip search operators that Tavily doesn't support."""
    # Remove site: operators
    query = re.sub(r'\bsite:\S+', '', query, flags=re.IGNORECASE)
    # Remove inurl: intitle: etc
    query = re.sub(r'\b(inurl|intitle|filetype|allintitle):\S+', '', query, flags=re.IGNORECASE)
    # Remove standalone OR/AND
    query = re.sub(r'\s+\b(OR|AND)\b\s+', ' ', query)
    # Collapse whitespace
    return ' '.join(query.split())

def web_search(query: str, max_results: int | None = None) -> list[dict]:
    query = _sanitize_query(query)
    logger.info(f"🔎 Tavily search: {query!r} ...")
    
    max_results = max_results or MAX_SEARCH_RESULTS

    if not TAVILY_API_KEY:
        logger.error("TAVILY_API_KEY not set")
        return []

    logger.info(f"🔎 Tavily search: {query!r} (max_results={max_results})")

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_domains=INDIAN_RETAILERS,
            country="india",
        )
        results = response.get("results", [])
        logger.info(f"   ← {len(results)} results returned")

        if not results:
            logger.warning(
                f"   ⚠️ Zero results — try loosening INDIAN_RETAILERS "
                f"or removing country filter"
            )

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in results
        ]
    except Exception as e:
        logger.exception(f"Tavily search failed: {e}")
        return []