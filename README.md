# Daily Job Hunt

AI-powered job automation SaaS. Scrapes 8 job boards, matches using a consensus council of 24 LLMs, generates tailored LaTeX resumes and cover letters, finds real LinkedIn contacts, and tracks everything in a dashboard.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## Stack

- Backend: FastAPI + Supabase (Postgres, Auth, RLS) + AWS Lambda
- Frontend: React 19 + Tailwind v4 + Vite
- AI: 24 LLMs via Groq, Qwen, NVIDIA NIM, OpenRouter
- PDF: LaTeX via tectonic/pdflatex
- Storage: S3 + Google Drive
- Contacts: Serper.dev (Google Search API)
- Deploy: SAM (Lambda), Netlify (frontend), GitHub Actions (pipeline)

## Features

- 8 scrapers (LinkedIn, Adzuna, IrishJobs, Jobs.ie, GradIreland, YC, HN, JobSurface)
- AI consensus council (2 generate, 1 critiques, best wins)
- 3-perspective scoring (ATS, Hiring Manager, Tech Recruiter)
- Tailored 2-page LaTeX resumes with 3-of-5 project selection
- Human-voice cover letters (no AI giveaway phrases)
- Real LinkedIn profile URLs via Google Search
- Dark dashboard with filters, score badges, status tracking
- GDPR compliance (export, deletion, audit trail)
- Self-improvement loop (per-model quality ranking)

## Deploy

```bash
sam build && sam deploy
gh workflow enable "Daily Job Hunt"
```

## Database

Run in Supabase SQL Editor:
1. db/migrations/001_initial.sql
2. db/migrations/002_gdpr.sql

## Tests

```bash
python tests/e2e_test.py  # 86/86 passing
```
