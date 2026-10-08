"""Rebuild the local calibration corpus for web/src/lib/jdExtract.js.

READ-ONLY against production: one GET of `jobs`, one of `jobs_raw`.

Export real (JD body, apply_url) -> (title, company, location) triples.

Ground truth is the user's own hand-typed field values, so this measures the
extractor against what a human actually decided, not against my guess at it.

HEAD_CHARS matches the extractor's own window exactly. Truncating the fixture
to a different length than the code examines would make the measurement
describe a different population (CLAUDE.md #7).
"""
import json, os, pathlib, httpx
from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
h = {"apikey": key, "Authorization": f"Bearer {key}"}
HEAD_CHARS = 2500

r = httpx.get(f"{url}/rest/v1/jobs",
    params={"select": "title,company,location,apply_url,canonical_hash",
            "canonical_hash": "not.is.null", "limit": "40",
            "order": "first_seen.desc"}, headers=h, timeout=30)
r.raise_for_status()
rows = [x for x in r.json() if x.get("canonical_hash")]
hs = ",".join(x["canonical_hash"] for x in rows)
d = httpx.get(f"{url}/rest/v1/jobs_raw",
    params={"select": "job_hash,description", "job_hash": f"in.({hs})"},
    headers=h, timeout=60)
d.raise_for_status()
desc = {x["job_hash"]: (x.get("description") or "") for x in d.json()}

out = []
for row in rows:
    body = desc.get(row["canonical_hash"], "")
    if len(body) < 200:
        continue  # nothing to extract from; not a fair test case
    out.append({
        "hash": row["canonical_hash"],
        "jd": body[:HEAD_CHARS],
        "apply_url": row.get("apply_url") or "",
        "expect": {"title": row["title"] or "", "company": row["company"] or "",
                   "location": row["location"] or ""},
    })
p = (pathlib.Path(__file__).resolve().parents[1]
     / "web/src/lib/__fixtures__/real_jds.json")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(f"{len(out)} labelled cases -> {p}")
print(f"with a URL: {sum(1 for x in out if x['apply_url'])}")
for x in out:
    print(f"  {x['expect']['company']:16} {x['expect']['location'][:34]:36} {len(x['jd'])}c")

# The output is gitignored on purpose: it is this user's own job-search history
# and the repo is public. jdExtract.test.js pins the behaviours the corpus
# taught; corpus_measurement.test.js reports the rates when the file is here.
