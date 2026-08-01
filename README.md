# 🎯 JobRadar

> An automated, AI-powered job discovery pipeline — aggregates **14 sources**, eliminates noise with zero-cost heuristics, deduplicates across runs, ranks by relevance, scores with AI, and delivers priority alerts via **Email**. Runs daily, entirely on free-tier APIs.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Sources](https://img.shields.io/badge/Job%20Sources-14-blueviolet?style=flat)](#-job-sources)
[![Free Tier](https://img.shields.io/badge/API%20Cost-100%25%20Free%20Tier-brightgreen?style=flat)](#-performance--api-usage)

Built for freshers, interns, and early-career developers — but **fully configurable for any role, stack, domain, or location via `profile.yaml` alone**.

> **Forked from [reorx/jobradar](https://github.com/reorx/jobradar) and extensively modified** — replaced Telegram notifications with Gmail SMTP email alerts, added GitHub Actions CI/CD with persistent SQLite DB, recalibrated AI scoring for AI/ML fresher roles, expanded ATS company list with Indian AI startups, and tuned all sources and filters for the Indian job market.

---

## 🚀 How It Works

```
┌─────────────────────────────────────────────────┐
│         14 Job Sources (Concurrent)             │
│  ATS APIs · YC · Internshala · Naukri · Hirist  │
│  Fresher Blogs RSS · Serper · Jobicy · RemoteOK │
└────────────────────────┬────────────────────────┘
                         │ ~8,000–9,000 raw jobs
                         ▼
┌─────────────────────────────────────────────────┐
│           Multi-Key Deduplication               │
│  Title+Company+Location MD5 · Canonical URL MD5 │
│  Run-level + SQLite persistent — never repeat   │
└────────────────────────┬────────────────────────┘
                         │ ~600–800 new jobs
                         ▼
┌─────────────────────────────────────────────────┐
│         Smart Rule-Based Pre-Filter             │
│  Expiry · Blacklists · ATS Allowlist · Location │
│  RSS Tags · Experience · Company Cap            │
│        Drops ~90–95% with zero AI cost          │
└────────────────────────┬────────────────────────┘
                         │ ~50–150 eligible jobs
                         ▼
┌─────────────────────────────────────────────────┐
│         Heuristic Relevance Ranking             │
│  AI/ML stack · Python · Fresher · Recency       │
│       Best-fit jobs scored first, free          │
└────────────────────────┬────────────────────────┘
                         │ ranked, best-first
                         ▼
┌─────────────────────────────────────────────────┐
│     AI Scorer — Groq llama-3.1-8b-instant       │
│  Token-budget guard · 5s throttle               │
│  Few-shot calibrated 1–10 scale                 │
└────────────────────────┬────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
 Score ≥ 8 (Urgent)            Score 6–7 (Digest)
 Instant email alert           Daily digest email
```

---

## ✨ What I Changed From the Original

| Area | Original | This Fork |
|---|---|---|
| **Notifications** | Telegram bot | Gmail SMTP email (urgent + digest) |
| **Scheduling** | Manual / cron | GitHub Actions — runs daily, auto-commits DB |
| **DB persistence** | Resets each run | SQLite committed back to repo after each run |
| **AI model** | llama-4-scout (deprecated) | llama-3.1-8b-instant (500K TPD free tier) |
| **Scoring calibration** | Go/fintech candidate | AI/ML fresher — Python, LangChain, CV, RAG |
| **ATS companies** | Western-focused | Added Sarvam AI, Sigmoid, BrowserStack, Atlan, ElevenLabs, FamPay, Sprinto, and more Indian AI startups |
| **Serper queries** | Golang/TypeScript dorks | AI/ML intern dorks for Indian market |
| **Internshala keywords** | Backend/Go | Python, ML, DL, CV, LangChain, FastAPI |
| **Digest threshold** | Score 6+ | Score 6+ with strict experience enforcement |
| **Notified flag** | Not tracked | Tracked in DB — no job sent twice across runs |

---

## 🗂️ Project Structure

```
jobradar/
│
├── main.py                    # Entry point — orchestrates the full pipeline
├── profile.yaml               # YOUR MAIN CONFIG FILE (roles, skills, location, filters)
├── companies.yaml             # ATS company slugs — 9 platforms
│
├── sources/                   # Job fetchers — one file per source
│   ├── ats.py                 # 9-platform ATS polling
│   ├── naukri.py              # Naukri.com — Stage-1 filtered search
│   ├── internshala.py         # Internshala — optimized plain-HTTP scraper
│   ├── freshers_blogs.py      # 8+ Indian fresher blogs — concurrent RSS
│   ├── yc.py                  # YC jobs board — two-phase scraper
│   ├── serper.py              # Tiered Google dork discovery
│   ├── jobicy.py              # Jobicy.com — remote jobs JSON API
│   ├── remoteok.py            # RemoteOK — JSON API
│   ├── cutshort.py            # Cutshort.io (enabled, AI/ML queries)
│   ├── instahyre.py           # Instahyre (enabled, AI/ML queries)
│   ├── hirist.py              # Hirist.tech (disabled — timeouts)
│   ├── wellfound.py           # Wellfound (disabled — bot detection)
│   ├── hackernews.py          # HN "Who is Hiring?"
│   └── reddit.py              # Reddit RSS feeds
│
├── pipeline/                  # Processing stages
│   ├── dedup.py               # Dual-hash deduplication
│   ├── prefilter.py           # Rule-based hard filters
│   ├── ranker.py              # Heuristic relevance ranker
│   └── scorer.py              # Groq AI scorer — token-budgeted, calibrated
│
├── notify/
│   └── telegram_bot.py        # Email notifier (urgent alerts + digest + summary)
│
├── storage/
│   └── db.py                  # SQLite schema + dual-hash CRUD + notified flag tracking
│
├── .github/
│   └── workflows/
│       └── jobradar.yml       # GitHub Actions — daily run + DB persistence
│
├── data/                      # Auto-created at runtime
│   ├── profile.db             # Persistent SQLite database (committed to repo)
│   └── profile.log            # Rotating run logs
│
├── docs/
│   └── setup_guide.md         # Setup & customisation guide
│
├── requirements.txt
└── .env                       # API keys (never commit)
```

---

## 🛠️ Setup

### 1. Clone & Install

```bash
git clone https://github.com/MohammedThaher01/jobradar.git
cd jobradar
pip install -r requirements.txt
playwright install --with-deps chromium
```

### 2. Configure `profile.yaml`

Edit to match your skills, target roles, and location. The full file is already tuned for an AI/ML fresher profile — update your name, email, and any specifics.

### 3. Get API Keys

| Key | Where to get it | Free tier |
|---|---|---|
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) | 500K tokens/day |
| `SERPER_API_KEY` | [serper.dev](https://serper.dev) | 2,500 searches/month |
| `EMAIL_SENDER` | Your Gmail address | — |
| `EMAIL_PASSWORD` | Gmail App Password ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)) | Free |
| `EMAIL_RECEIVER` | Where alerts go (can be same as sender) | — |

### 4. Run Locally

```bash
# Validate config — no API calls
python main.py profile.yaml --dry-run

# Full run
python main.py profile.yaml
```

### 5. Automate with GitHub Actions

Add the 5 keys above as **GitHub Secrets** (repo → Settings → Secrets → Actions). The workflow in `.github/workflows/jobradar.yml` runs daily at 9 AM IST, commits the DB back to the repo for persistence, and emails you results automatically.

---

## 📊 Performance & API Usage

### Typical Run Stats

| Stage | Count | Notes |
|---|---|---|
| Raw jobs fetched | ~8,000–9,000 | ATS polling + all sources |
| After deduplication | ~600–800 new | Dual-hash SQLite lookups |
| After pre-filter | ~100–150 eligible | Zero AI cost |
| After AI scorer | ~80–100 scored | Token-budget limited |
| Urgent alerts | 1–5 per run | Score ≥ 8 |
| Digest jobs | 5–15 per run | Score 6–7 |

### API Free Tier Usage

| API | Per run | Free tier | Status |
|---|---|---|---|
| Groq (llama-3.1-8b-instant) | ~200K tokens | 500K tokens/day | ✅ Safe |
| Serper.dev | 25 queries | 2,500/month | ✅ ~750/month |
| Gmail SMTP | ~3 emails | Unlimited | ✅ Free |

---

## 🔭 Future Goals

1. **Fix disabled sources** — Wellfound, Instahyre, Cutshort need proper headless browser or API workarounds
2. **Application tracking UI** — lightweight web dashboard to track applied/rejected/interviewing status
3. **Multi-profile support** — `profiles/` directory, one YAML per candidate
4. **Global coverage** — make India-specific sources opt-in, expand remote-first boards

---

## 🤝 Contributing

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the source contract, pipeline wiring guide, and PR process.

---

## 📝 License

MIT