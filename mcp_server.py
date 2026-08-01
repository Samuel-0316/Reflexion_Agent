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

# Note: We do not hardcode specialty stores or brands. Instead, verify_merchant_authority
# uses dynamic algorithmic e-commerce heuristics (TLD, localization, URL paths, and retail signals).



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

    # 2. Dynamic Algorithmic E-Commerce Authority Scoring (No hardcoded store lists!)
    # Evaluates Indian TLD/localization, URL structure, and retail indicators.
    score = 0.50
    reasons = []

    # Indian localization / domain TLD signal (+0.25)
    is_indian_domain = (
        host.endswith(".in")
        or host.endswith(".co.in")
        or host.endswith(".org.in")
        or host.endswith(".net.in")
        or "-india" in host
        or "india." in host
        or "/in/" in raw
        or "/in-en/" in raw
        or "en-in" in raw
    )
    if is_indian_domain:
        score += 0.25
        reasons.append("Indian domain TLD or regional e-commerce localization")

    # Retail / E-commerce URL structure & product path signal (+0.15)
    retail_paths = ["/product", "/p/", "/item/", "/buy", "/shop", "-price-", "/dp/"]
    has_retail_path = any(p in raw.lower() for p in retail_paths)
    if has_retail_path:
        score += 0.15
        reasons.append("Valid D2C product/listing URL path structure")

    # Specialty store / brand website keyword signal (+0.10)
    store_keywords = ["audio", "sound", "store", "shop", "kart", "cart", "tech", "electronics", "retail", "buy", "mall", "lifestyle", "brand", "direct"]
    has_store_keyword = any(kw in host.lower() for kw in store_keywords)
    if has_store_keyword:
        score += 0.10
        reasons.append("Recognized retail/brand e-commerce indicator")

    # Total trust evaluation
    score = min(round(score, 2), 0.95)
    is_auth = (score >= 0.80)
    status = "VERIFIED_SPECIALTY_OR_BRAND_STORE" if is_auth else "GENERAL_INDIAN_MERCHANT"

    res = {
        "domain": host,
        "trust_score": score,
        "status": status,
        "authoritative": is_auth,
        "reason": f"Algorithmic verification ({'; '.join(reasons) if reasons else 'Unverified domain root'})",
    }
    return json.dumps(res)



# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
