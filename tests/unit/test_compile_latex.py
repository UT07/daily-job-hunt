"""Unit tests for compile_latex Lambda."""
from unittest.mock import patch, MagicMock


def _make_s3_mock(tex_content=r"\documentclass{article}\begin{document}Hello\end{document}"):
    """Return a mock S3 client with get_object returning tex_content."""
    mock_s3 = MagicMock()
    body_mock = MagicMock()
    body_mock.read.return_value = tex_content.encode("utf-8")
    mock_s3.get_object.return_value = {"Body": body_mock}
    return mock_s3


SAMPLE_EVENT = {
    "tex_s3_key": "resumes/user-1/hash-abc.tex",
    "job_hash": "hash-abc",
    "user_id": "user-1",
    "doc_type": "resume",
}


def test_tectonic_not_available_returns_graceful_fallback():
    """When tectonic binary is absent (FileNotFoundError), handler returns error key instead of raising."""
    s3 = _make_s3_mock()

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = s3

    with patch("compile_latex.boto3", mock_boto3), \
         patch("compile_latex.subprocess.run", side_effect=FileNotFoundError("tectonic not found")):
        import compile_latex
        result = compile_latex.handler(SAMPLE_EVENT, None)

    assert result["error"] == "tectonic_not_available"
    assert result["pdf_s3_key"] is None
    assert result["tex_s3_key"] == SAMPLE_EVENT["tex_s3_key"]
    assert result["job_hash"] == "hash-abc"
    assert result["user_id"] == "user-1"
    assert result["doc_type"] == "resume"
