"""Static assertions on the migration. Cheap, and catches the mistakes that
actually happen: wrong dimensions, missing index, missing RLS.
"""
import pathlib

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260922000000_pgvector.sql"
)


def test_migration_exists():
    assert MIGRATION.is_file()


def test_enables_vector_extension():
    assert "create extension if not exists vector" in MIGRATION.read_text().lower()


def test_uses_768_dimensions_everywhere():
    sql = MIGRATION.read_text().lower()
    assert "vector(768)" in sql
    # A stray 1536 means a copy-paste from OpenAI docs; Gemini 004 is 768.
    assert "vector(1536)" not in sql


def test_creates_hnsw_cosine_indexes():
    sql = MIGRATION.read_text().lower()
    assert sql.count("using hnsw") >= 2
    assert "vector_cosine_ops" in sql


def test_enables_rls_on_new_table():
    sql = MIGRATION.read_text().lower()
    assert "alter table" in sql and "resume_bullets" in sql
    assert "enable row level security" in sql
