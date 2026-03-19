# Daily Job Hunt

Automated job search pipeline + self-service web app for resume tailoring.

Scrapes 7 job boards daily, matches jobs against your profile using AI (3-perspective scoring), generates tailored LaTeX resumes and cover letters, uploads PDFs to Google Drive, and sends email summaries. Includes a React landing page where you can paste any job description and get a tailored resume on demand.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions (daily cron)                                │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ Scrape  │→ │ Match   │→ │ Tailor   │→ │ Compile PDF │  │
│  │ 7 sites │  │ AI 3x   │  │ + Score  │  │ (tectonic)  │  │
│  └─────────┘  └─────────┘  └──────────┘  └──────┬──────┘  │
│                                                   │         │
│  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │         │
│  │ Email    │← │ Excel    │← │ Upload S3 +    │←─┘         │
│  │ Summary  │  │ Tracker  │  │ Google Drive   │             │
│  └──────────┘  └──────────┘  └────────────────┘             │
│                                                              │
│  ┌──────────────────┐                                        │
│  │ Self-Improvement  │  ← analyzes results, suggests fixes   │
│  └──────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Landing Page (React + FastAPI)                              │
│  ┌──────────────┐    ┌──────────────────────────────┐       │
│  │ React SPA    │───→│ FastAPI Backend               │       │
│  │ (Netlify)    │    │ /api/score                    │       │
│  │              │    │ /api/tailor                    │       │
│  │              │    │ /api/cover-letter              │       │
│  │              │    │ /api/contacts                  │       │
│  └──────────────┘    └──────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Features

### Automated Pipeline
- **7 job sources**: Adzuna, LinkedIn, IrishJobs, Jobs.ie, GradIreland, YC Work at a Startup, HN Who is Hiring
- **Multi-geo search**: Ireland (primary), India (remote), US (remote + sponsorship)
- **AI matching**: 3-perspective scoring (ATS, Hiring Manager, Technical Recruiter) with batch processing
- **Resume tailoring**: LaTeX resumes tailored per job, iteratively improved to 85+ scores
- **Cover letters**: Professional LaTeX cover letters for each matched job
- **LinkedIn contacts**: 3-4 strategic contacts per job with intro message templates
- **Smart deduplication**: Fuzzy matching on company name (80%) + title (85%)
- **Seen-jobs persistence**: Never re-processes the same job across runs
- **Multi-provider AI**: Groq → DeepSeek → OpenRouter → Claude failover (all free tiers)
- **Self-improvement**: Analyzes run results, detects weak spots, suggests fixes

### Landing Page
- **Score any JD**: Paste a job description, get instant 3-perspective score
- **Tailor on demand**: Generate a tailored resume PDF for any job
- **Cover letter generation**: One-click cover letter creation
- **LinkedIn contacts**: Find relevant people to reach out to
- **React + Tailwind**: Clean, responsive UI
- **Deployable to Netlify**: Zero-config deployment

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+ (for frontend)
- At least one AI API key (Groq is free: https://console.groq.com)

### 1. Install Dependencies

```bash
# Backend
pip install -r requirements.txt
playwright install chromium

# Frontend
cd web && npm install && cd ..
```

### 2. Configure

```bash
# Copy and edit config
cp config.yaml config.yaml.bak

# Set API keys (get free keys from groq.com, deepseek.com)
export GROQ_API_KEY="your-key"
export DEEPSEEK_API_KEY="your-key"

# Optional: Google Drive upload
# Place google_credentials.json in project root

# Optional: Email notifications
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="your-app-password"
```

### 3. Run the Pipeline

```bash
# Full run: scrape → match → tailor → compile → upload → email
python main.py

# Dry run: scrape + match only (no PDFs)
python main.py --dry-run

# Scrape only: just see what's out there
python main.py --scrape-only
```

### 4. Run the Landing Page (local)

```bash
# Terminal 1: Backend
uvicorn app:app --reload --port 8000

# Terminal 2: Frontend
cd web && npm run dev
```

Open http://localhost:5173 — paste a JD, click "Score Resume".

## Deployment

### Frontend (Netlify)

```bash
# Install Netlify CLI
npm i -g netlify-cli

# Deploy
cd web
netlify login
netlify init        # Link to a Netlify site
netlify deploy --prod
```

Set environment variable in Netlify dashboard:
- `VITE_API_URL` = your API Gateway URL (e.g., `https://xyz.execute-api.us-east-1.amazonaws.com/prod`)

### Backend (AWS Lambda)

```bash
# Install AWS SAM CLI
pip install aws-sam-cli

# Deploy
sam build
sam deploy --guided

# Set parameters when prompted:
# GroqApiKey, DeepSeekApiKey, OpenRouterApiKey, GoogleCredentialsJson
```

### GitHub Actions (Daily Automation)

The pipeline runs automatically via `.github/workflows/daily_job_hunt.yml`:
- **Schedule**: Weekdays at 7:00 UTC
- **Manual trigger**: Actions → Run workflow

Required GitHub Secrets:
| Secret | Description |
|--------|-------------|
| `GROQ_API_KEY` | Groq API key (free) |
| `DEEPSEEK_API_KEY` | DeepSeek API key (free) |
| `OPENROUTER_API_KEY` | OpenRouter API key (free) |
| `ADZUNA_APP_ID` | Adzuna API app ID |
| `ADZUNA_APP_KEY` | Adzuna API key |
| `GOOGLE_CREDENTIALS_JSON` | Base64-encoded service account JSON |
| `GMAIL_ADDRESS` | Gmail address for notifications |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `AWS_ACCESS_KEY_ID` | AWS credentials for S3 |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials for S3 |
| `S3_BUCKET` | S3 bucket name |

## Project Structure

```
daily-job-hunt/
├── main.py                    # Pipeline orchestrator (10 steps)
├── app.py                     # FastAPI backend (5 endpoints)
├── config.yaml                # Configuration (profiles, search, API keys)
│
├── scrapers/                  # Job scrapers (7 sources)
│   ├── base.py                # Base Job class
│   ├── adzuna_scraper.py      # Adzuna API
│   ├── linkedin_scraper.py    # LinkedIn (Playwright stealth)
│   ├── irishjobs_scraper.py   # IrishJobs.ie
│   ├── jobs_ie_scraper.py     # Jobs.ie
│   ├── gradireland_scraper.py # GradIreland
│   ├── yc_wats_scraper.py     # YC Work at a Startup
│   └── hn_scraper.py          # Hacker News hiring threads
│
├── ai_client.py               # Multi-provider AI client with failover
├── matcher.py                 # 3-perspective job matching (batch)
├── tailorer.py                # LaTeX resume tailoring
├── resume_scorer.py           # Score + iterative improvement loop
├── cover_letter.py            # Cover letter generation
├── contact_finder.py          # LinkedIn contact finder
├── latex_compiler.py          # LaTeX → PDF (tectonic/pdflatex)
├── excel_tracker.py           # Excel tracker generation
├── s3_uploader.py             # S3 upload with presigned URLs
├── drive_uploader.py          # Google Drive upload
├── email_notifier.py          # Gmail notification with HTML summary
├── self_improver.py           # Self-improvement analysis engine
│
├── resumes/                   # Base LaTeX resumes
│   ├── sre_devops.tex
│   └── fullstack.tex
│
├── web/                       # React frontend (Vite + Tailwind)
│   ├── src/
│   │   ├── App.jsx            # Main app component
│   │   ├── api.js             # API client
│   │   └── components/        # UI components
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
│
├── template.yaml              # AWS SAM deployment template
├── netlify.toml               # Netlify deployment config
├── requirements.txt           # Python dependencies
├── requirements-web.txt       # FastAPI dependencies
│
├── .github/workflows/
│   └── daily_job_hunt.yml     # GitHub Actions daily cron
│
├── output/                    # Generated artifacts (gitignored)
│   ├── YYYY-MM-DD/            # Daily output folder
│   │   ├── resumes/           # Tailored PDFs
│   │   ├── cover-letters/     # Cover letter PDFs
│   │   └── run_metadata.json  # Run stats
│   ├── job_tracker.xlsx       # Master Excel tracker
│   ├── seen_jobs.json         # Dedup persistence
│   └── improvement_report.json # Self-improvement analysis
│
└── docs/                      # Design specs
```

## Self-Improvement Loop

After each pipeline run, `self_improver.py` automatically:

1. **Analyzes score distributions** — identifies which perspective (ATS/HM/TR) is consistently weakest
2. **Detects keyword gaps** — finds tech skills that appear in 50%+ of matched JDs but may be underrepresented in resumes
3. **Monitors scraper health** — flags scrapers with 0 results or high error rates
4. **Tracks match rates** — alerts if too few jobs are matching or scoring 85+
5. **Generates actionable suggestions** — specific steps to improve resume quality
6. **Auto-disables broken scrapers** — removes failing scrapers from config

Results are saved to `output/improvement_report.json` and logged to console.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check, lists loaded resumes |
| `POST` | `/api/score` | Score JD against base resume (3-perspective) |
| `POST` | `/api/tailor` | Tailor resume + compile PDF, return Drive URL |
| `POST` | `/api/cover-letter` | Generate cover letter PDF, return Drive URL |
| `POST` | `/api/contacts` | Find LinkedIn contacts + intro messages |

### Example: Score a Job

```bash
curl -X POST http://localhost:8000/api/score \
  -H "Content-Type: application/json" \
  -d '{
    "job_description": "We are looking for a DevOps Engineer...",
    "job_title": "DevOps Engineer",
    "company": "Google",
    "resume_type": "sre_devops"
  }'
```

## License

Private project by Utkarsh Singh.
