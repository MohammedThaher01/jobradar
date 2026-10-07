import os
import random
import logging
import requests

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL     = "https://google.serper.dev/search"
MAX_SERPER_CALLS = 25
TIER_1_BUDGET    = 10


# ── TIER 1 — unique sources only Serper can find (always runs) ────────────────
_TIER1_QUERIES = [
    # Indian AI startups not in ATS list
    'site:sarvam.ai jobs intern',
    'site:krutrim.com careers intern',
    'site:ola.com/careers "machine learning" intern',
    'site:mphasis.com careers "AI" intern 2026',
    'site:fractal.ai careers fresher',
    'site:sigmoid.com careers intern',
    'site:yellowai.com careers intern',
    'site:haptik.ai careers intern',
    'site:mudrex.com careers intern',
    'site:uniphore.com careers intern',
    # Google Forms / hidden applications
    '"forms.gle" "AI intern" india 2026',
    '"forms.gle" "machine learning intern" india',
    '"forms.gle" "python developer intern" india',
    # Notion / Typeform job pages
    '"notion.so" "we are hiring" "AI" intern india',
    '"typeform.com" "AI intern" india',
    # Freshteam / Zoho / Keka (Indian ATS not in companies.yaml)
    'site:freshteam.com "machine learning" intern',
    'site:zohorecruit.com "AI" OR "python" intern india',
    'site:keka.com "AI" intern india',
    # Off-campus drives
    '"off campus" "AI intern" india 2026',
    '"hiring" "AI engineer intern" india 2026',
    '"python intern" "generative AI" india 2026',
    '"LLM intern" india 2026',
    '"computer vision intern" india 2026',
    '"RAG" "intern" india 2026',
    '"langchain" "intern" india 2026',
    '"deep learning intern" india 2026',
]

# ── TIER 2 — role + stack dorks ───────────────────────────────────────────────
_TIER2_QUERIES = [
    # AI/ML roles
    '"machine learning intern" india site:linkedin.com/jobs',
    '"AI intern" india 2026 -site:naukri.com',
    '"generative AI intern" india',
    '"NLP intern" india 2026',
    '"computer vision intern" india',
    '"deep learning intern" india',
    '"LLM engineer intern" india',
    '"AI research intern" india 2026',
    '"MLOps intern" india 2026',
    '"data science intern" india 2026 fresher',
    # Python/Backend
    '"python backend intern" india 2026',
    '"django intern" india 2026',
    '"fastapi intern" india',
    '"python developer intern" india fresher',
    '"backend intern" "python" india 2026',
    # Remote-friendly
    '"remote AI intern" india 2026',
    '"work from home" "AI intern" india',
    '"remote ML intern" india',
    '"remote python intern" india 2026',
    # Startup hiring
    '"YC startup" "ML intern" india',
    '"series A" "AI intern" india',
    '"seed stage" "machine learning" intern india',
    '"AI startup" hiring intern india 2026',
    # GenAI specific
    '"prompt engineer intern" india',
    '"vector database" intern india',
    '"RAG pipeline" intern india',
    '"fine tuning" intern india 2026',
    '"hugging face" intern india',
    '"langchain developer" intern india',
    # CV specific
    '"yolo" intern india',
    '"opencv" intern india',
    '"object detection" intern india',
]

# ── TIER 3 — broad fallback ───────────────────────────────────────────────────
_TIER3_QUERIES = [
    'site:lever.co "machine learning" intern india',
    'site:greenhouse.io "AI" intern india',
    'site:ashbyhq.com "python" intern india',
    'site:lever.co "python" intern india',
    'site:greenhouse.io "machine learning" india',
    'site:ashbyhq.com "AI" intern india',
    'site:lever.co "generative AI" intern',
    'site:greenhouse.io "computer vision" intern',
    'site:ashbyhq.com "deep learning" intern',
    '"software engineer intern" "machine learning" india 2026',
    '"SDE intern" "AI" india 2026',
    '"fresher" "machine learning" "python" india 2026',
    '"entry level" "AI engineer" india',
    '"0-1 years" "machine learning" india',
    '"B.Tech" "AI intern" india 2026',
]


def _run_query(query: str, num: int = 10) -> list[dict]:
    if not SERPER_API_KEY:
        return []
    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("organic", [])
    except Exception as e:
        logger.warning(f"Serper query failed {query!r}: {e}")
        return []


def fetch_serper_jobs(profile: dict) -> list[dict]:
    tier1 = random.sample(_TIER1_QUERIES, min(TIER_1_BUDGET, len(_TIER1_QUERIES)))
    remaining = MAX_SERPER_CALLS - len(tier1)

    tier2_pool = _TIER2_QUERIES.copy()
    random.shuffle(tier2_pool)
    tier2 = tier2_pool[:remaining]
    remaining -= len(tier2)

    tier3 = []
    if remaining > 0:
        tier3_pool = _TIER3_QUERIES.copy()
        random.shuffle(tier3_pool)
        tier3 = tier3_pool[:remaining]

    logger.info(
        f"Serper pool: {len(_TIER1_QUERIES)} tier-1 | {len(_TIER2_QUERIES)} tier-2 | "
        f"{len(_TIER3_QUERIES)} tier-3. Running: {len(tier1)} + {len(tier2)} + {len(tier3)} = "
        f"{len(tier1)+len(tier2)+len(tier3)} queries"
    )

    jobs = []
    seen_urls = set()

    for query in tier1 + tier2 + tier3:
        results = _run_query(query)
        for r in results:
            url = r.get("link", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Skip known job aggregators already covered by dedicated sources
            skip_domains = ["naukri.com", "internshala.com", "hirist.com", "indeed.com"]
            if any(d in url for d in skip_domains):
                continue

            jobs.append({
                "title":       r.get("title", ""),
                "company":     r.get("displayLink", ""),
                "location":    "India",
                "description": r.get("snippet", ""),
                "url":         url,
                "source":      "serper",
                "salary":      "",
                "posted_at":   "",
            })

    logger.info(f"Serper: {len(jobs)} unique results")
    return jobs
