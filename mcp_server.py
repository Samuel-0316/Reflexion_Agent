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
import urllib.parse

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from tavily import TavilyClient

# ── Load environment ─────────────────────────────────────────────────
load_dotenv()

TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
MAX_SEARCH_RESULTS: int = int(os.getenv("MAX_SEARCH_RESULTS", "6"))

logger = logging.getLogger("mcp_server")

# ── Authoritative Domain Registries ──────────────────────────────────
AUTHORITATIVE_MARKETPLACES = {
    "amazon.in": "Amazon India Marketplace",
    "flipkart.com": "Flipkart Marketplace",
    "croma.com": "Croma Electronics",
    "reliancedigital.in": "Reliance Digital",
    "tatacliq.com": "Tata CLiQ Marketplace",
    "vijaysales.com": "Vijay Sales Electronics",
    "jiomart.com": "JioMart India",
    "myntra.com": "Myntra Fashion & Lifestyle",
    "nykaa.com": "Nykaa Beauty & Tech",
    "snapdeal.com": "Snapdeal Marketplace",
}

VERIFIED_SPECIALTY_STORES = {
    "headphonezone.in": "Headphone Zone India (Audiophile Specialty)",
    "theaudiostore.in": "The Audio Store India (Audiophile Specialty)",
    "conceptkart.com": "Concept Kart India (Audio & Tech Distributor)",
    "apple.com": "Apple Official Store India",
    "sony.co.in": "Sony Official India Store",
    "samsung.com": "Samsung Official India Store",
    "dyson.in": "Dyson Official India Store",
    "nothing.tech": "Nothing Official India Store",
    "oneplus.in": "OnePlus Official India Store",
    "boat-lifestyle.com": "boAt Lifestyle India",
    "mi.com": "Xiaomi / Mi Official India Store",
    "realme.com": "Realme Official India Store",
    "ouraring.com": "Oura Official Store",
}



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
    """Search for product prices across the Indian web and e-commerce retailers.

    Uses Tavily with country='india' scoping to dynamically search across all
    major Indian online stores, official brand websites (.in), and authorized
    distributors, returning a JSON array of results with title, url, and content.

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
            country="india",
        )
        results = response.get("results", [])
        logger.info(f"   ← {len(results)} results returned")

        if not results:
            logger.warning(
                "   ⚠️ Zero results returned from Tavily (country='india')"
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


@mcp.tool()
def verify_merchant_authority(domain_or_url: str) -> str:
    """Verify if a domain or URL is an Authoritative Indian Marketplace or Verified Specialty Store.

    Used by AI evaluation agents to determine if a single search result source
    can be trusted as authoritative without requiring a second cross-reference.

    Args:
        domain_or_url: The full URL or root domain (e.g. "https://www.headphonezone.in/..." or "headphonezone.in").

    Returns:
        A JSON string containing:
        - domain: extracted root domain
        - trust_score: float (0.0 to 1.0)
        - status: string classification
        - authoritative: bool (True if trust_score >= 0.8)
        - reason: explanation of merchant authority
    """
    raw = domain_or_url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urllib.parse.urlparse(raw)
        host = parsed.netloc or parsed.path
    except Exception:
        host = raw
    host = host.lower().removeprefix("www.")

    logger.info(f"🛡️  MCP verify_merchant_authority: {host!r} (from {domain_or_url!r})")

    # 1. Authoritative Marketplace
    for domain, name in AUTHORITATIVE_MARKETPLACES.items():
        if host == domain or host.endswith("." + domain):
            res = {
                "domain": host,
                "trust_score": 1.0,
                "status": "AUTHORITATIVE_MARKETPLACE",
                "authoritative": True,
                "reason": f"Verified Major Indian E-Commerce Marketplace ({name})",
            }
            return json.dumps(res)

    # 2. Verified Specialty or Brand Store
    for domain, name in VERIFIED_SPECIALTY_STORES.items():
        if host == domain or host.endswith("." + domain):
            res = {
                "domain": host,
                "trust_score": 0.95,
                "status": "VERIFIED_SPECIALTY_OR_BRAND_STORE",
                "authoritative": True,
                "reason": f"Verified Authoritative Indian Brand / Specialty Distributor ({name})",
            }
            return json.dumps(res)

    # 3. Dynamic Heuristic: Indian TLD (.in, .co.in, .org.in)
    if host.endswith(".in") or host.endswith(".co.in") or host.endswith(".org.in"):
        res = {
            "domain": host,
            "trust_score": 0.70,
            "status": "GENERAL_INDIAN_MERCHANT",
            "authoritative": False,
            "reason": "Valid Indian domain TLD (.in), but general merchant requiring multi-source corroboration",
        }
        return json.dumps(res)

    # 4. Unverified / Foreign
    res = {
        "domain": host,
        "trust_score": 0.50,
        "status": "UNVERIFIED_OR_FOREIGN_MERCHANT",
        "authoritative": False,
        "reason": "Unverified or non-Indian domain root",
    }
    return json.dumps(res)



# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
