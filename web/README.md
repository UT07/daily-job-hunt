# NaukriBaba Frontend

React + Vite + Tailwind. Deployed to Netlify.

## Local Development

```bash
# 1. Copy env template and fill in Supabase values
cp .env.example .env.local
# Leave VITE_API_URL empty (Vite proxy handles /api → localhost:8000)

# 2. Install deps
npm install

# 3. Start dev server (serves source, reads .env.local)
npx vite
# → http://localhost:5173
```

**Important: use `npx vite` (dev server), NOT `npx vite preview`.**

`vite preview` serves the built bundle from `.env.production`, which hardcodes
the production API Gateway URL. This makes localhost hit the production
backend, which is usually not what you want.

## Run Backend Too

```bash
# In another terminal, from project root:
uvicorn app:app --reload --port 8000
```

The Vite dev server proxies `/api/*` to `http://localhost:8000`.

## Build for Production

```bash
npm run build  # Uses .env.production
```

## Tech Stack

- React 19
- Vite
- Tailwind CSS v4
- Zustand (state)
- React Router v7
- Supabase JS client
