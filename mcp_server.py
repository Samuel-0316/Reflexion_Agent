"""
MCP Server — exposes a `search_prices` tool backed by Tavily,
restricted to Indian retailers.

Transport: stdio (spawned as a subprocess by mcp_client.py)

Usage:
    python mcp_server.py
"""

# MCP Inspector link: npx -y @modelcontextprotocol/inspector@latest python mcp_server.py

from __future__ import annotations

import json
import logging
import os
import re

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from tavily import TavilyClient

# ── Load environment ─────────────────────────────────────────────────
load_dotenv()

TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
MAX_SEARCH_RESULTS: int = int(os.getenv("MAX_SEARCH_RESULTS", "6"))

logger = logging.getLogger("mcp_server")

# ── Indian retailer allow-list ───────────────────────────────────────
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

# ── FastMCP server instance ──────────────────────────────────────────
mcp = FastMCP(
    "PriceSearchServer",
    instructions=(
        "An MCP server that searches for product prices on Indian "
        "e-commerce retailers using the Tavily search API."
    ),
)


# ── Helpers ──────────────────────────────────────────────────────────

def _sanitize_query(query: str) -> str:
    """Strip search operators that Tavily doesn't support."""
    # Remove site: operators
    query = re.sub(r'\bsite:\S+', '', query, flags=re.IGNORECASE)
    # Remove inurl: intitle: etc
    query = re.sub(
        r'\b(inurl|intitle|filetype|allintitle):\S+', '', query,
        flags=re.IGNORECASE,
    )
    # Remove standalone OR/AND
    query = re.sub(r'\s+\b(OR|AND)\b\s+', ' ', query)
    # Collapse whitespace
    return ' '.join(query.split())


# ── MCP Tool ─────────────────────────────────────────────────────────

@mcp.tool()
def search_prices(query: str, max_results: int | None = None) -> str:
    """Search for product prices on Indian e-commerce retailers.

    Uses Tavily to search across major Indian online stores
    (Amazon.in, Flipkart, Croma, Reliance Digital, etc.) and returns
    a JSON array of results, each with title, url, and content fields.

    Args:
        query: The search query string (e.g. "iPhone 15 128GB price India").
        max_results: Maximum number of results to return (default: 6).

    Returns:
        A JSON string containing an array of objects with keys: title, url, content.
    """
    query = _sanitize_query(query)
    max_results = max_results or MAX_SEARCH_RESULTS

    logger.info(f"🔎 MCP search_prices: {query!r} (max_results={max_results})")

    if not TAVILY_API_KEY:
        logger.error("TAVILY_API_KEY not set")
        return json.dumps([])

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
                "   ⚠️ Zero results — try loosening INDIAN_RETAILERS "
                "or removing country filter"
            )

        cleaned = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in results
        ]
        return json.dumps(cleaned)
    except Exception as e:
        logger.exception(f"Tavily search failed: {e}")
        return json.dumps([])


# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
