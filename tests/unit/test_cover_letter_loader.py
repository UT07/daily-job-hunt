from unittest.mock import MagicMock
from shared.cover_letter_loader import load_cover_letter


_TEX_CONTENT = r"\section*{Cover Letter}Dear Hiring Manager,\\I am applying..."


class TestLoadCoverLetter:
    def test_loads_from_s3_and_strips_latex(self):
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_TEX_CONTENT.encode()))
        }

        result = load_cover_letter(
            user_id="user-1", job_hash="abc123", s3_client=s3, bucket="my-bucket"
        )

        assert result is not None
        assert "Dear Hiring Manager" in result["text"]
        assert "section" not in result["text"]
        assert result["source"] == "tailored"
        s3.get_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="users/user-1/cover_letters/abc123_cover.tex",
        )

    def test_returns_none_when_s3_object_missing(self):
        s3 = MagicMock()
        from botocore.exceptions import ClientError
        s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject"
        )

        result = load_cover_letter(
            user_id="user-1", job_hash="abc123", s3_client=s3, bucket="my-bucket"
        )

        assert result is None

    def test_returns_none_on_other_s3_error(self):
        s3 = MagicMock()
        from botocore.exceptions import ClientError
        s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}}, "GetObject"
        )

        result = load_cover_letter(
            user_id="user-1", job_hash="abc123", s3_client=s3, bucket="my-bucket"
        )

        assert result is None

    def test_constructs_correct_s3_key(self):
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b""))
        }

        load_cover_letter(
            user_id="UID-XYZ", job_hash="HASH-123", s3_client=s3, bucket="bkt"
        )

        s3.get_object.assert_called_once_with(
            Bucket="bkt",
            Key="users/UID-XYZ/cover_letters/HASH-123_cover.tex",
        )
