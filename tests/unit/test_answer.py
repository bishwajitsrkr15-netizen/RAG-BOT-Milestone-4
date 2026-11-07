import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from src.retrieval.answer import AnswerError, _read_env_setting, answer_question
from src.retrieval.retrieve import RetrievedChunk


class AnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "config" / "app.yaml"
        self.config_path.parent.mkdir()
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "sources": [
                        {
                            "id": "hdfc-mutual-fund-amc",
                            "url": "https://groww.in/mutual-funds/amc/hdfc-mutual-funds",
                        },
                        {
                            "id": "hdfc-large-cap",
                            "url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
                        },
                    ],
                    "scheme_aliases": {
                        "hdfc-large-cap": ["HDFC Large Cap", "Large Cap"]
                    },
                    "llm": {
                        "provider": "groq",
                        "api_key_env": "GROQ_API_KEY",
                        "model_env": "LLM_MODEL",
                    },
                }
            ),
            encoding="utf-8",
        )
        self.chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text="HDFC Large Cap Fund\nExpense ratio is 0.5%.",
            source_id="hdfc-large-cap",
            url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            title="HDFC Large Cap Fund",
            distance=0.2,
            source_updated_at=None,
            metadata={},
        )

    def test_reads_streamlit_secrets_when_environment_variable_is_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.retrieval.answer.st.secrets", {"GROQ_API_KEY": "test-key"}):
                result = _read_env_setting("GROQ_API_KEY", Path(self.temp.name))

        self.assertEqual(result, "test-key")

    def test_supported_factual_answer_uses_grounded_source_and_freshness(self) -> None:
        result = answer_question(
            "What is the expense ratio of HDFC Large Cap Fund?",
            self.config_path,
            retrieval_callable=lambda question, path: [self.chunk],
            completion_callable=lambda *args, **kwargs: "The expense ratio is 0.5%.",
        )
        self.assertEqual(result.status, "answered")
        self.assertEqual(result.answer, "The expense ratio is 0.5%.")
        self.assertEqual(result.citation_url, self.chunk.url)
        self.assertEqual(
            result.last_updated_from_sources,
            "Last updated from sources: source update date unavailable",
        )

    def test_no_evidence_returns_cited_fallback_without_llm_call(self) -> None:
        called = False

        def completion(*args: Any, **kwargs: Any) -> str:
            nonlocal called
            called = True
            return "Unsupported fact."

        result = answer_question(
            "What is the minimum SIP of HDFC Large Cap Fund?",
            self.config_path,
            retrieval_callable=lambda question, path: [],
            completion_callable=completion,
        )
        self.assertEqual(result.status, "no_evidence")
        self.assertFalse(called)
        self.assertEqual(result.citation_url, self.chunk.url)

    def test_advice_and_performance_are_refused_before_retrieval(self) -> None:
        def unexpected_retrieval(*args: Any, **kwargs: Any) -> list[RetrievedChunk]:
            raise AssertionError("guarded questions must not be retrieved")

        advice = answer_question(
            "Should I buy HDFC Large Cap Fund?",
            self.config_path,
            retrieval_callable=unexpected_retrieval,
        )
        performance = answer_question(
            "Compare returns of HDFC Large Cap Fund",
            self.config_path,
            retrieval_callable=unexpected_retrieval,
        )
        self.assertEqual(advice.status, "refused")
        self.assertEqual(performance.status, "refused")
        self.assertEqual(advice.citation_url, self.chunk.url)

    def test_sensitive_query_is_not_echoed_or_sent_downstream(self) -> None:
        def unexpected_retrieval(*args: Any, **kwargs: Any) -> list[RetrievedChunk]:
            raise AssertionError("sensitive question must not be retrieved")

        result = answer_question(
            "My PAN is ABCDE1234F, what is my fund status?",
            self.config_path,
            retrieval_callable=unexpected_retrieval,
        )
        self.assertEqual(result.status, "privacy_notice")
        self.assertNotIn("ABCDE1234F", result.answer)

    def test_unsupported_output_shape_fails_explicitly(self) -> None:
        with self.assertRaises(AnswerError):
            answer_question(
                "What is the expense ratio of HDFC Large Cap Fund?",
                self.config_path,
                retrieval_callable=lambda question, path: [self.chunk],
                completion_callable=lambda *args, **kwargs: (
                    "First sentence. Second sentence. Third sentence."
                ),
            )
        with self.assertRaises(AnswerError):
            answer_question(
                "What is the expense ratio of HDFC Large Cap Fund?",
                self.config_path,
                retrieval_callable=lambda question, path: [self.chunk],
                completion_callable=lambda *args, **kwargs: (
                    "Here is more information https://example.com"
                ),
            )


if __name__ == "__main__":
    unittest.main()
