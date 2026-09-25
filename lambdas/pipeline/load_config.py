import hashlib
import logging

import boto3

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


# Process-level caches — same rationale and shape as ai_helper.get_param /
# ai_helper.get_supabase: both used to redo their full work on EVERY call, a
# live SSM GetParameter round trip with KMS decryption, plus a fresh
# create_client() on top of two of those. Memoized in place rather than by
# importing ai_helper's copies: that would resolve here (same CodeUri) but is a
# wider refactor than this change, and the scrapers cannot do it at all.
# reset_caches() below is the escape hatch for a rotated parameter or a
# per-call test.
_param_cache: dict[str, str] = {}
_supabase = None


def get_param(name):
    # `not in` rather than a falsy check: a parameter that is legitimately the
    # empty string must stay cached, not be re-fetched on every call forever.
    if name not in _param_cache:
        _param_cache[name] = _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return _param_cache[name]


def get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client
        _supabase = create_client(
            get_param("/naukribaba/SUPABASE_URL"),
            get_param("/naukribaba/SUPABASE_SERVICE_KEY"),
        )
    return _supabase


def reset_caches():
    """Drop the memoized SSM parameters and Supabase client.

    Mirrors ai_helper.reset_caches(). Leaves the boto3 client alone — that one
    is a connection holder, not a cached value.
    """
    global _supabase
    _param_cache.clear()
    _supabase = None


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
        "queries": ["software engineer"],
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

    # Compute a short hash of the search parameters for cache-keying downstream
    query_str = "|".join(config.get("queries", []))
    location_str = "|".join(config.get("locations", []))
    config["query_hash"] = hashlib.md5(f"{query_str}|{location_str}".encode()).hexdigest()[:12]

    logger.info(f"[load_config] User {user_id}: {len(config.get('queries', []))} queries, min_score={config.get('min_match_score', 60)}")
    return config
