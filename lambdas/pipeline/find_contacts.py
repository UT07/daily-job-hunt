import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]

    db = get_supabase()

    job = db.table("jobs").select("company, title").eq("user_id", user_id) \
        .eq("job_hash", job_hash).execute()
    if not job.data:
        return {"job_hash": job_hash, "contacts": []}
    job = job.data[0]

    # Search for relevant LinkedIn profiles
    search_query = f'{job["company"]} {job["title"]} hiring manager recruiter site:linkedin.com/in'

    try:
        apify_key = get_param("/naukribaba/APIFY_API_KEY")
        from apify_client import ApifyClient
        client = ApifyClient(apify_key)

        run = client.actor("apify/google-search-scraper").call(
            run_input={"queries": search_query, "maxPagesPerQuery": 1, "resultsPerPage": 5},
            timeout_secs=60
        )
        results = client.dataset(run["defaultDatasetId"]).list_items().items

        contacts = []
        for r in results:
            organic = r.get("organicResults", [])
            for item in organic[:5]:
                url = item.get("url", "")
                if "linkedin.com/in/" in url:
                    contacts.append({
                        "name": item.get("title", "").split(" - ")[0].strip(),
                        "url": url,
                        "headline": item.get("description", "")[:200],
                    })

        # Save contacts to job record
        if contacts:
            db.table("jobs").update({"linkedin_contacts": json.dumps(contacts[:3])}) \
                .eq("user_id", user_id).eq("job_hash", job_hash).execute()

        logger.info(f"[contacts] Found {len(contacts)} for {job_hash}")
        return {"job_hash": job_hash, "user_id": user_id, "contacts_found": len(contacts)}

    except Exception as e:
        logger.warning(f"[contacts] Failed for {job_hash}: {e}")
        return {"job_hash": job_hash, "user_id": user_id, "contacts_found": 0}
