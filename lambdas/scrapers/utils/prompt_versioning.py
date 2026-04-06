"""Prompt version management -- store, load, and rollback prompt versions."""
from datetime import datetime


def load_active_prompt(db, user_id: str, prompt_name: str) -> dict | None:
    """Load the currently active prompt version."""
    result = (
        db.table("prompt_versions")
        .select("*")
        .eq("user_id", user_id)
        .eq("prompt_name", prompt_name)
        .is_("active_to", "null")
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create_prompt_version(
    db,
    user_id: str,
    prompt_name: str,
    content: str,
    created_by: str = "manual",
) -> int:
    """Create a new prompt version. Deactivates the current active version."""
    current = load_active_prompt(db, user_id, prompt_name)
    if current:
        db.table("prompt_versions").update(
            {"active_to": datetime.now().isoformat()}
        ).eq("id", current["id"]).execute()
        new_version = current["version"] + 1
    else:
        new_version = 1

    db.table("prompt_versions").insert(
        {
            "user_id": user_id,
            "prompt_name": prompt_name,
            "version": new_version,
            "content": content,
            "created_by": created_by,
        }
    ).execute()
    return new_version


def rollback_prompt(db, user_id: str, prompt_name: str) -> bool:
    """Rollback to the previous prompt version. Returns True if rollback succeeded."""
    current = load_active_prompt(db, user_id, prompt_name)
    if not current or current["version"] <= 1:
        return False

    # Deactivate current
    db.table("prompt_versions").update(
        {"active_to": datetime.now().isoformat()}
    ).eq("id", current["id"]).execute()

    # Reactivate previous
    previous = (
        db.table("prompt_versions")
        .select("*")
        .eq("user_id", user_id)
        .eq("prompt_name", prompt_name)
        .eq("version", current["version"] - 1)
        .execute()
    )

    if previous.data:
        db.table("prompt_versions").update({"active_to": None}).eq(
            "id", previous.data[0]["id"]
        ).execute()
        return True
    return False
