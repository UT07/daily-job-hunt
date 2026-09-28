import hashlib
import logging

import boto3

from shared.location_policy import normalize_locations

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lazy SSM client — boto3.client at module load forces AWS_DEFAULT_REGION
# on every importer (including unit tests + runtime-import smoke).
# Same pattern as ai_helper.py shipped in PR #23.
_ssm = None


def _get_ssm():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def get_param(name):
    return _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def load_config_with_adjustments(base_config: dict, user_id: str) -> dict:
    """Load base config and merge active pipeline adjustments.

    Precedence: auto_applied FIRST, then approved OVERWRITES
    (later in loop = higher priority = wins).
    User manual overrides in base_config are preserved unless an
    adjustment explicitly overrides them.
    """
    config = dict(base_config)

    # Query active adjustments from Supabase
    db = get_supabase()
    result = db.table("pipeline_adjustments").select("*").in_(
        "status", ["auto_applied", "approved"]
    ).eq("user_id", user_id).execute()

    active_adjustments = result.data or []

    # Sort: auto_applied first (key=0), then approved overwrites (key=1)
    for adj in sorted(active_adjustments, key=lambda a: 0 if a["status"] == "auto_applied" else 1):
        payload = adj.get("payload", {})
        for key, value in payload.items():
            config[key] = value

    # Store which adjustments are active (for pipeline_runs tracking)
    config["_active_adjustments"] = [a["id"] for a in active_adjustments]

    return config


def handler(event, context):
    user_id = event["user_id"]
    db = get_supabase()

    # If user_id is "default", find the first active user — allows EventBridge
    # rule to use Input: '{"user_id": "default"}' without hardcoding a UUID.
    if user_id == "default":
        users = db.table("users").select("id").limit(1).execute()
        if not users.data:
            logger.error("[load_config] No users found")
            return {"error": "no_users", "user_id": "default"}
        user_id = users.data[0]["id"]
        logger.info(f"[load_config] Resolved 'default' to user {user_id}")

    # Load base search config
    search_config = db.table("user_search_configs") \
        .select("*").eq("user_id", user_id).execute()

    config = search_config.data[0] if search_config.data else {
        # No single job title is a domain-neutral default -- "software
        # engineer" (the old fallback) silently assumed every unconfigured
        # user was in tech, which returns nothing useful (and looks broken)
        # for a nurse, accountant, or teacher. Empty is the honest answer:
        # scrape nothing for a user who hasn't told us what they're looking
        # for, rather than guess wrong. Downstream scrapers already treat an
        # empty `queries` list as "nothing to search" (they only fall back
        # to their own default when the key is missing entirely, and this
        # dict always supplies the key), so this doesn't crash anything --
        # it just means the pipeline does no work until Settings has a real
        # query, which is the correct "configure your search" signal.
        "queries": [],
        "locations": ["ireland"],
        "sources": ["linkedin", "indeed", "adzuna", "hn", "yc", "gradireland"],
        "min_match_score": 60,
    }

    # Load self-improvement adjustments
    adjustments = db.table("self_improvement_config") \
        .select("*").eq("user_id", user_id).execute()

    for adj in (adjustments.data or []):
        if adj["config_type"] == "query_weights":
            config["queries"] = sorted(config.get("queries", []),
                key=lambda q: adj["config_data"].get(q, 0.5), reverse=True)
        elif adj["config_type"] == "scraper_weights":
            config["skip_scrapers"] = [s for s, w in adj["config_data"].items() if w < 0.1]
        elif adj["config_type"] == "scoring_threshold":
            config["min_match_score"] = adj["config_data"].get("threshold", 60)
        elif adj["config_type"] == "keyword_emphasis":
            config["emphasis_keywords"] = adj["config_data"].get("keywords", [])

    # Merge any active pipeline adjustments (auto_applied first, then approved overwrites)
    config = load_config_with_adjustments(config, user_id)

    config["user_id"] = user_id

    # Normalise `locations` into a flat list of strings, unconditionally.
    #
    # Two reasons this is not left as whatever the jsonb column happened to
    # hold. First, the state machine now passes it to the scrapers as
    # `"locations.$": "$.locations"`, and a JSONPath reference to a key that
    # is absent from the state input is not a validation error -- it is a
    # States.Runtime failure at execution time, i.e. the whole RunScrapers
    # branch dies. Setting the key on every path makes that unreachable.
    # Second, the column is jsonb and the project writes two shapes into it
    # (Settings.jsx writes a flat list, config.yaml uses
    # {"primary": [...]}), so the scrapers would otherwise each have to
    # re-derive the shape -- which is the duplication this change removes.
    #
    # Until 2026-09-28 the only thing this value was used for was salting
    # query_hash below; it reached no scraper at all.
    config["locations"] = normalize_locations(config.get("locations"))

    # Compute a short hash of the search parameters for cache-keying downstream
    query_str = "|".join(config.get("queries", []))
    location_str = "|".join(config["locations"])
    config["query_hash"] = hashlib.md5(f"{query_str}|{location_str}".encode()).hexdigest()[:12]

    logger.info(
        f"[load_config] User {user_id}: {len(config.get('queries', []))} queries, "
        f"locations={config['locations']}, min_score={config.get('min_match_score', 60)}"
    )
    return config
