import os
import re
import json
import time
import yaml
import logging
from datetime import datetime, timezone
from dateutil import parser as dateutil_parser
from groq import Groq
from storage.db import save_job, is_already_notified
from sources.freshers_blogs import fetch_full_description
from sources.naukri import lazy_fetch_naukri_detail
from pipeline.prefilter import _CLOSED_PHRASES, _DEADLINE_CONTEXT_RE
from pipeline.ranker import rank_eligible_jobs

logger = logging.getLogger(__name__)

MODEL        = "meta-llama/llama-4-scout-17b-16e-instruct"
REQ_INTERVAL = 5.0
_last_call_ts = 0.0

TOKEN_BUDGET_PER_RUN  = 200_000
SYSTEM_PROMPT_TOKENS  = 800
RESPONSE_TOKENS       = 400
CHARS_PER_TOKEN       = 4


def _groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set in .env")
    return Groq(api_key=api_key)


def _throttle():
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < REQ_INTERVAL:
        time.sleep(REQ_INTERVAL - elapsed)
    _last_call_ts = time.time()


def load_profile(path="profile.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


_FEW_SHOT_EXAMPLES = """
## CALIBRATION EXAMPLES (use these to anchor your scoring scale)

### Example A — Score 9 (near-perfect match)
Job: "Backend Intern – Go/Golang" at Koinbase (crypto exchange), Bangalore/Remote
Description excerpt: "We're building a high-throughput order matching engine in Go.
  You'll work on our gRPC microservices, PostgreSQL schemas, and Redis caching layer.
  0–1 years experience. Stipend: ₹25,000/month. Apply before July 2026."
→ Correct score: 9
→ Reasoning: Golang + gRPC + PostgreSQL + Redis = exact stack match. Crypto/fintech
  domain matches Zaraba project signal. Remote/Bangalore is acceptable. Fresher role.
  Stipend above minimum. Only reason it isn't 10: no mention of equity/ESOPs and
  company is less well-known.
→ apply_urgency: "high"

### Example B — Score 3 (tech role, poor fit)
Job: "Junior DevOps Engineer" at TechCorp, Pune (on-site)
Description excerpt: "2+ years with AWS, Terraform, Jenkins CI/CD pipelines required.
  Must have experience managing production Kubernetes clusters."
→ Correct score: 3
→ Reasoning: DevOps is on the role blacklist. Requires 2+ years experience (hard
  reject signal). On-site Pune is borderline acceptable but the experience requirement
  alone makes this unfit. Terraform/Jenkins are not in the candidate's stack.
→ apply_urgency: "low"
"""


def build_scoring_prompt(job: dict, profile: dict) -> str:
    candidate = profile["candidate"]
    today = datetime.now().strftime("%B %d, %Y")

    projects_text = "\n".join(
        f"- {p['name']}: {p['description']} | Signals: {p['relevance_signal']}"
        for p in candidate.get('projects', [])
    )

    return f"""You are a job relevance scorer for a specific candidate. Score how relevant a job posting is for this person.

Today's Date: {today}

## CANDIDATE PROFILE

Name: {candidate['name']}
Current level: Fresher / 0 years experience (B.Tech student, graduating Aug 2026)

Target roles (priority order):
{chr(10).join('- ' + r for r in candidate['roles']['primary'])}
Also acceptable: {', '.join(candidate['roles']['secondary'])}

Tech stack:
- Strong: {', '.join(candidate['skills']['strong'])}
- Learning: {', '.join(candidate['skills']['learning'])}

Projects (all are strong portfolio signals):
{projects_text}

Location: {candidate['location']['base']}
Acceptable locations: {', '.join(candidate['location']['acceptable'])}

High-priority industries (bonus): {', '.join(candidate['industries']['high_priority'])}
Medium-priority industries: {', '.join(candidate['industries']['medium_priority'])}

## JOB POSTING

Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Location: {job.get('location', 'N/A')}
Salary/Stipend: {job.get('salary', 'Not mentioned')}
Source: {job.get('source', 'N/A')}

Job Description:
{job.get('description', 'No description available')[:3000]}

## SCORING RULES

Score 1-10 (use the calibration examples in the system prompt as anchors):
- 10 = Perfect match (AI/ML/GenAI/CV intern or fresher + India/Remote + strong stack match)
- 8-9 = Very strong (AI/ML/GenAI/CV/Backend intern, Python + relevant frameworks, relevant company)
- 6-7 = Good (backend adjacent or AI adjacent, potentially relevant, worth applying)
- 4-5 = Weak (tangentially related)
- 1-3 = Not relevant

Mandatory rules — apply in this exact order, override scoring bonuses:
1. EXPIRY CHECK (highest priority): If the description contains ANY of these signals—
   application closed / hiring closed / recruitment closed / position filled /
   no longer accepting / deadline has passed / last date was [past date]—
   set score=1, apply_urgency="expired", expired=true. Do NOT apply any bonuses.
2. Requires >1 year experience: score 1-2 (pre-filter miss, still log it)
3. Location is outside India AND in-office only: score=1
4. Post is older than 2 months (check posted dates in description): score 1-3
5. No stipend mentioned or explicitly unpaid: -2 penalty
6. Python mentioned in a relevant context: +2 to base score
7. LangChain / LangGraph / RAG / LLM mentioned: +2 to base score
8. YOLOv8 / OpenCV / Computer Vision mentioned: +2 to base score
9. FastAPI / Django backend role: +1 to base score
10. AI/ML/GenAI company: +2 to base score
11. Any of candidate's projects are directly relevant: +2 to base score
12. Internshala source with matching stipend (>=10000 INR/month): slight bonus

TOKEN SAVING RULES — IMPORTANT:
- If score < 6: set reason="", highlights=[], red_flags=[] — write nothing for these fields.
- If score >= 6: fill in reason, highlights, and red_flags normally.

Return ONLY a valid JSON object, no markdown fences:
{{
  "score": <integer 1-10>,
  "expired": <true if application is closed/deadline passed, false otherwise>,
  "reason": "<2-3 sentences IF score>=6, else empty string>",
  "highlights": ["<reason 1>", "<reason 2>", "<reason 3> — IF score>=6, else []"],
  "red_flags": ["<issue if any> — IF score>=6, else []"],
  "python_match": <true/false>,
  "ai_match": <true/false>,
  "apply_urgency": "<high/medium/low/expired>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    desc = job.get("description", "")
    if len(desc) < 100 and job.get("url") and "freshers_blogs" in job.get("source", ""):
        logger.debug(f"Lazy-fetching JD for {job.get('title', '?')}")
        fetched = fetch_full_description(job["url"])
        if fetched:
            job["description"] = fetched
            desc = job["description"]

    if job.get("_naukri_job_id") and len(desc) < 150:
        logger.debug(f"Lazy-fetching Naukri JD for {job.get('title', '?')}")
        fetched = lazy_fetch_naukri_detail(job)
        if fetched:
            job["description"] = fetched
            desc = job["description"]
    job.pop("_naukri_job_id", None)

    expiry_signal: re.Match | None = _CLOSED_PHRASES.search(desc)
    if not expiry_signal:
        now = datetime.now(timezone.utc)
        for m in _DEADLINE_CONTEXT_RE.finditer(desc):
            try:
                dl = dateutil_parser.parse(m.group(1).strip(), dayfirst=False)
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                if dl < now:
                    expiry_signal = m
                    break
            except Exception:
                pass

    if expiry_signal:
        logger.info(
            f"Pre-Groq expiry detected for '{job.get('title','?')}': "
            f"'{expiry_signal.group(0).strip()[:60]}' — skipping scorer"
        )
        job["score"]       = 1
        job["expired"]     = True
        job["reason"]      = ""
        job["highlights"]  = ""
        job["red_flags"]   = ""
        job["urgency"]     = "expired"
        return job

    _throttle()

    client = _groq_client()

    try:
        prompt = build_scoring_prompt(job, profile)

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise job relevance scorer. "
                        "Always respond with valid JSON only, no markdown.\n"
                        + _FEW_SHOT_EXAMPLES
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.1,
            max_tokens=512,
        )

        text = response.choices[0].message.content.strip()

        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()

        result = json.loads(text)

        job["score"]       = int(result.get("score", 0))
        job["expired"]     = bool(result.get("expired", False))
        job["reason"]      = result.get("reason", "")
        job["highlights"]  = ", ".join(result.get("highlights", []))
        job["red_flags"]   = ", ".join(result.get("red_flags", []))
        job["urgency"]     = result.get("apply_urgency", "low")

        logger.info(
            f"Scored: {job['title']} @ {job['company']} -> {job['score']}/10 [{job['urgency']}]"
        )
        return job

    except Exception as e:
        logger.error(f"Groq scoring failed for {job.get('title', '?')}: {e}")
        job["score"]       = -1
        job["reason"]      = f"Scoring error: {e}"
        job["highlights"]  = ""
        job["red_flags"]   = ""
        job["urgency"]     = "low"
        return job


def _estimate_prompt_tokens(job: dict) -> int:
    desc_chars = len(job.get("description", "")[:3000])
    user_prompt_tokens = (desc_chars + 400) // CHARS_PER_TOKEN
    return SYSTEM_PROMPT_TOKENS + user_prompt_tokens + RESPONSE_TOKENS


def score_all(
    jobs: list[dict],
    profile: dict | None = None,
    db_path: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    if profile is None:
        profile = load_profile()

    jobs = rank_eligible_jobs(
        jobs,
        weights=profile.get("ranker_weights"),
        profile=profile,
    )

    max_ai_jobs = profile.get("hard_reject", {}).get("max_ai_jobs_per_run", 200)
    if len(jobs) > max_ai_jobs:
        dropped_cap = len(jobs) - max_ai_jobs
        jobs = jobs[:max_ai_jobs]
        logger.warning(
            f"Hard fallback cap: trimmed {dropped_cap} lowest-ranked jobs "
            f"(max_ai_jobs_per_run={max_ai_jobs}). Increase cap in profile.yaml if needed."
        )

    urgent        : list[dict] = []
    digest        : list[dict] = []
    low           : list[dict] = []
    expired_count : int        = 0
    skipped_notified: int      = 0
    tokens_used   : int        = 0
    budget_skipped: int        = 0

    logger.info(
        f"Scoring up to {len(jobs)} ranked jobs with Groq ({MODEL}) "
        f"| token budget: {TOKEN_BUDGET_PER_RUN:,}"
    )

    for job in jobs:
        # ── Skip jobs already notified in a previous run ──────────────────
        if is_already_notified(job, db_path):
            skipped_notified += 1
            logger.debug(f"Already notified, skipping: {job.get('title','?')} @ {job.get('company','?')}")
            continue

        # ── Token budget check ─────────────────────────────────────────────
        job_tokens = _estimate_prompt_tokens(job)
        if tokens_used + job_tokens > TOKEN_BUDGET_PER_RUN:
            budget_skipped += 1
            continue

        tokens_used += job_tokens
        scored_job = score_job(job, profile)

        scored_job.pop("_heuristic_score", None)
        scored_job.pop("_heuristic_reasons", None)

        if scored_job.get("expired") or scored_job.get("urgency") == "expired":
            expired_count += 1
            continue

        # Bucket first, then persist with correct notified flag
        if scored_job["score"] >= 8:
            urgent.append(scored_job)
            notified_flag = 1
        elif scored_job["score"] >= 6:
            digest.append(scored_job)
            notified_flag = 2
        else:
            low.append(scored_job)
            notified_flag = 0

        if scored_job["score"] >= 5:
            save_job(
                scored_job,
                score      = scored_job["score"],
                reason     = scored_job.get("reason", ""),
                highlights = scored_job.get("highlights", ""),
                red_flags  = scored_job.get("red_flags", ""),
                notified   = notified_flag,
                db_path    = db_path,
            )

    if skipped_notified:
        logger.info(f"Skipped {skipped_notified} already-notified jobs from previous runs.")
    if budget_skipped:
        logger.info(
            f"Token budget: {tokens_used:,}/{TOKEN_BUDGET_PER_RUN:,} tokens used. "
            f"Skipped {budget_skipped} lower-ranked jobs to stay under limit."
        )
    else:
        logger.info(f"Token budget: {tokens_used:,}/{TOKEN_BUDGET_PER_RUN:,} tokens used.")

    logger.info(
        f"Scoring complete: {len(urgent)} urgent, {len(digest)} digest, "
        f"{len(low)} low, {expired_count} expired (dropped)"
    )
    return urgent, digest, low