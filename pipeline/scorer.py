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

MODEL = "llama-3.3-70b-versatile"
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
Job: "AI/ML Intern" at Sarvam AI, Bangalore/Remote
Description excerpt: "We are building India's sovereign LLM. Looking for a fresher
  to work on RAG pipelines, LangChain agents, and fine-tuning open-source models.
  0-1 years experience. Stipend: Rs.20,000/month."
→ Correct score: 9
→ Reasoning: RAG + LangChain + LLM fine-tuning = exact stack match. Indian AI company
  is high-priority domain. Remote/Bangalore acceptable. Fresher role with good stipend.
→ apply_urgency: "high"

### Example B — Score 3 (looks relevant, actually not)
Job: "Python and Kubernetes Software Engineer - Data, Workflows, AI/ML" at Canonical
Description excerpt: "3+ years of Python experience required. Must have production
  Kubernetes cluster management experience. Enterprise Linux packaging knowledge needed."
→ Correct score: 3
→ Reasoning: Despite Python and AI/ML in title, this requires 3+ years experience
  which is a hard reject. Kubernetes cluster management is not the candidate's stack.
  Enterprise Linux packaging is irrelevant. Not a fresher or intern role.
→ apply_urgency: "low"

### Example C — Score 2 (experience mismatch)
Job: "Applied AI Engineer" at HackerRank, Hybrid Bengaluru
Description excerpt: "1-4 years of software engineering experience required.
  Must have shipped ML models to production."
→ Correct score: 2
→ Reasoning: Requires 1-4 years experience — hard reject for a fresher. 'Applied AI
  Engineer' sounds relevant but the experience bar makes it unfit.
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
- 10 = Perfect match (AI/ML/GenAI/CV intern or fresher + India/Remote + strong stack match + stipend mentioned)
- 8-9 = Very strong (AI/ML/GenAI/CV intern or fresher role, Python + relevant frameworks, India/Remote, stipend mentioned or implied)
- 6-7 = Good (backend or AI adjacent, potentially relevant, worth applying, fresher-friendly)
- 4-5 = Weak (tangentially related, experience mismatch, or missing key signals)
- 1-3 = Not relevant, experience too high, wrong role, or outside India with no remote option

MANDATORY HARD RULES — apply these first, they override all bonuses:
1. EXPIRY CHECK: If description contains application closed / hiring closed / position filled /
   no longer accepting / deadline has passed → score=1, apply_urgency="expired", expired=true
2. Requires >1 year experience explicitly → score MAX 3, apply_urgency="low"
3. Requires 1+ years experience → score MAX 4, apply_urgency="low"
4. Role is DevOps, QA, Test, Android, iOS, .NET, Golang, Java Developer → score=1
5. Location is outside India AND strictly on-site only → score=1
6. Post older than 2 months → score MAX 3

SCORING BONUSES (only apply if hard rules don't cap the score):
+ Python in a relevant AI/ML/backend context: +2
+ LangChain / LangGraph / RAG / LLM / GenAI mentioned: +2
+ YOLOv8 / OpenCV / Computer Vision / Object Detection: +2
+ FastAPI / Django backend role: +1
+ AI/ML/GenAI company or product: +2
+ Candidate's projects directly relevant: +2
+ Explicitly fresher / 0-1 years / intern role: +1
+ Stipend >= Rs.10,000/month mentioned: +1
+ Remote or India location confirmed: +1

PENALTIES:
- No stipend mentioned AND not a well-known company: -1
- Explicitly unpaid / no stipend / volunteer: -3
- Requires frontend-only skills (React, Angular) with no backend/AI: -2
- Vague job description under 200 chars: -1

TOKEN SAVING RULES:
- If score < 6: set reason="", highlights=[], red_flags=[]
- If score >= 6: fill reason, highlights, red_flags normally

Return ONLY valid JSON, no markdown fences:
{{
  "score": <integer 1-10>,
  "expired": <true/false>,
  "reason": "<2-3 sentences IF score>=6, else empty string>",
  "highlights": ["<reason 1>", "<reason 2>", "<reason 3>"],
  "red_flags": ["<issue if any>"],
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
                        "You are a precise job relevance scorer for an AI/ML fresher candidate. "
                        "Always respond with valid JSON only, no markdown. "
                        "Be strict about experience requirements — if a job requires more than 1 year "
                        "of experience, it must score 3 or below regardless of tech stack match.\n"
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
        if is_already_notified(job, db_path):
            skipped_notified += 1
            logger.debug(f"Already notified, skipping: {job.get('title','?')} @ {job.get('company','?')}")
            continue

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