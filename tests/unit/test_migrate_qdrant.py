import unittest
from unittest.mock import patch

from scripts.migrate_qdrant import _ensure_source_id_index


class QdrantMigrationTests(unittest.TestCase):
    def test_ensures_keyword_index_for_source_filter(self) -> None:
        with patch("scripts.migrate_qdrant._request") as request:
            _ensure_source_id_index(
                "https://qdrant.example",
                "hdfc-mutual-funds",
                "test-api-key",
            )

        request.assert_called_once_with(
            "https://qdrant.example/collections/hdfc-mutual-funds/index",
            "test-api-key",
            method="PUT",
            body={"field_name": "source_id", "field_schema": "keyword"},
        )


if __name__ == "__main__":
    unittest.main()
