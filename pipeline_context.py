"""Pipeline execution context — bundles user, config, and services for a pipeline run."""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from user_profile import UserProfile
    from ai_client import AIClient
    from db_client import SupabaseClient


@dataclass
class PipelineContext:
    """Everything needed to run the pipeline for one user.

    Replaces passing ``config: dict`` through every function.
    Modules receive this instead and pull what they need.
    """

    user: UserProfile
    resumes: Dict[str, str]  # {resume_key: tex_content}
    search_config: dict  # Queries, locations, etc.
    ai_client: AIClient
    config: dict  # Full config.yaml (for scraper settings, output paths, etc.)
    output_dir: Path = field(default_factory=lambda: Path("output"))
    db: Optional[SupabaseClient] = None  # None in single-user/local mode
    run_date: str = ""

    # ── Factory methods ────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict) -> PipelineContext:
        """Build context from config.yaml for single-user backward-compat mode.

        This lets the existing pipeline keep working without Supabase.
        Loads user from config profile section, resumes from tex files,
        search config from config search section.
        """
        from user_profile import UserProfile
        from ai_client import AIClient
        from datetime import datetime

        user = UserProfile.from_config(config)

        # Load resumes from tex files referenced in config
        resumes: Dict[str, str] = {}
        for key, info in config.get("resumes", {}).items():
            tex_path = Path(info["tex_path"])
            if tex_path.exists():
                resumes[key] = tex_path.read_text(encoding="utf-8")

        search_config = config.get("search", {})
        ai_client = AIClient.from_config(config)

        output_dir = Path(config.get("output", {}).get("base_dir", "output"))

        return cls(
            user=user,
            resumes=resumes,
            search_config=search_config,
            ai_client=ai_client,
            config=config,
            output_dir=output_dir,
            run_date=datetime.now().strftime("%Y-%m-%d"),
        )

    @classmethod
    def from_db(cls, user_id: str, db: SupabaseClient, config: dict) -> PipelineContext:
        """Build context from Supabase for a specific user.

        Used in multi-tenant mode when running the pipeline for a user
        from the API or scheduler.
        """
        from user_profile import UserProfile
        from ai_client import AIClient
        from datetime import datetime

        # Load user profile
        user_row = db.get_user(user_id)
        if not user_row:
            raise ValueError(f"User {user_id} not found")
        user = UserProfile.from_db_row(user_row)

        # Load resumes
        resume_rows = db.get_resumes(user_id)
        resumes: Dict[str, str] = {}
        for r in resume_rows:
            if r.get("tex_content"):
                resumes[r["resume_key"]] = r["tex_content"]

        # Load search config
        search_config = db.get_search_config(user_id) or {}

        ai_client = AIClient.from_config(config)
        output_dir = Path(config.get("output", {}).get("base_dir", "output"))

        return cls(
            user=user,
            resumes=resumes,
            search_config=search_config,
            ai_client=ai_client,
            config=config,
            output_dir=output_dir,
            db=db,
            run_date=datetime.now().strftime("%Y-%m-%d"),
        )

    # ── Derived directory properties ───────────────────────────────────────

    @property
    def daily_dir(self) -> Path:
        """Output directory for today's run."""
        d = self.output_dir / self.run_date
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def resumes_dir(self) -> Path:
        """Directory for generated resume PDFs."""
        d = self.daily_dir / "resumes"
        d.mkdir(exist_ok=True)
        return d

    @property
    def coverletters_dir(self) -> Path:
        """Directory for generated cover letter PDFs."""
        d = self.daily_dir / "cover_letters"
        d.mkdir(exist_ok=True)
        return d
