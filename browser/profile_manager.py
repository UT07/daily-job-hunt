"""Chrome profile persistence — save/load user profiles to/from S3 as tar.gz."""

import logging
import shutil
import tarfile
from io import BytesIO
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)

PROFILE_DIR = Path("/tmp/chrome-profile")

# Cache directories to strip before saving (100MB → 5-10MB)
CACHE_DIRS = [
    "Cache", "Code Cache", "GPUCache", "Service Worker",
    "DawnCache", "DawnGraphiteCache", "ShaderCache",
]


def load_profile(s3_bucket: str, user_id: str, platform: str) -> bool:
    """Download and extract Chrome profile from S3. Returns True if profile existed."""
    key = f"sessions/{user_id}/{platform}/profile.tar.gz"
    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=s3_bucket, Key=key)
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=BytesIO(obj["Body"].read()), mode="r:gz") as tar:
            tar.extractall(PROFILE_DIR)
        logger.info(f"Loaded profile from s3://{s3_bucket}/{key}")
        return True
    except s3.exceptions.NoSuchKey:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("No existing profile found, starting fresh")
        return False
    except Exception as e:
        logger.warning(f"Failed to load profile: {e}")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        return False


def save_profile(s3_bucket: str, user_id: str, platform: str) -> None:
    """Clean up Chrome profile and upload to S3 as tar.gz."""
    if not PROFILE_DIR.exists():
        logger.warning("Profile directory does not exist, skipping save")
        return

    _cleanup_profile()

    key = f"sessions/{user_id}/{platform}/profile.tar.gz"
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(PROFILE_DIR), arcname=".")
    buf.seek(0)

    s3 = boto3.client("s3")
    s3.put_object(Bucket=s3_bucket, Key=key, Body=buf.read())
    logger.info(f"Saved profile to s3://{s3_bucket}/{key}")


def _cleanup_profile() -> None:
    """Remove lock files and cache directories to reduce profile size."""
    # Delete Chrome lock files (prevent corruption on next load)
    for pattern in ["**/Singleton*", "**/*.lock"]:
        for lock_file in PROFILE_DIR.glob(pattern):
            lock_file.unlink(missing_ok=True)

    # Strip cache directories
    for cache_dir in CACHE_DIRS:
        cache_path = PROFILE_DIR / "Default" / cache_dir
        if cache_path.exists():
            shutil.rmtree(cache_path, ignore_errors=True)
