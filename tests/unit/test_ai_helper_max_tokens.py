from unittest.mock import patch, MagicMock


def test_ai_complete_cached_forwards_max_tokens():
    with patch("lambdas.pipeline.ai_helper.ai_complete") as inner, \
         patch("lambdas.pipeline.ai_helper.get_supabase") as get_db:
        # Cache miss path
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = []
        db.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        get_db.return_value = db
        inner.return_value = {"content": "ok", "provider": "p", "model": "m"}

        from lambdas.pipeline.ai_helper import ai_complete_cached
        ai_complete_cached("hi", system="sys", temperature=0.3, max_tokens=300)

        inner.assert_called_once()
        kwargs = inner.call_args.kwargs
        assert kwargs.get("max_tokens") == 300
        assert kwargs.get("temperature") == 0.3


def test_ai_complete_cached_default_max_tokens_unchanged():
    with patch("lambdas.pipeline.ai_helper.ai_complete") as inner, \
         patch("lambdas.pipeline.ai_helper.get_supabase") as get_db:
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = []
        db.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        get_db.return_value = db
        inner.return_value = {"content": "ok", "provider": "p", "model": "m"}

        from lambdas.pipeline.ai_helper import ai_complete_cached
        ai_complete_cached("hi", system="sys")

        kwargs = inner.call_args.kwargs
        # Backwards compat: default should still be 4096 (or whatever ai_complete defaults to)
        assert kwargs.get("max_tokens", 4096) == 4096
