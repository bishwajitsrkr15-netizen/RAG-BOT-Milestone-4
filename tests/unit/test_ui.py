import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class FAQUiTests(unittest.TestCase):
    def test_imports_when_started_from_the_app_directory(self) -> None:
        app_path = Path(__file__).resolve().parents[2] / "src" / "ui" / "app.py"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy; runpy.run_path('app.py')",
            ],
            cwd=app_path.parent,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_welcome_disclaimer_and_three_examples_render(self) -> None:
        app_path = Path(__file__).resolve().parents[2] / "src" / "ui" / "app.py"
        with patch("src.retrieval.retrieve.read_setting", return_value=None):
            app = AppTest.from_file(str(app_path)).run()

        self.assertEqual(len(app.exception), 0)
        button_labels = [button.label for button in app.button]
        self.assertIn("Get a factual answer", button_labels)
        self.assertIn(
            "What is the expense ratio of HDFC Large Cap Fund?",
            button_labels,
        )
        self.assertIn(
            "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
            button_labels,
        )
        self.assertIn(
            "What is the minimum SIP for HDFC Mid Cap Fund?",
            button_labels,
        )
        rendered_markdown = "\n".join(element.value for element in app.markdown)
        self.assertIn("A clearer answer to your fund questions.", rendered_markdown)
        self.assertIn("Facts-only. No investment advice.", rendered_markdown)

    def test_empty_submission_shows_input_prompt(self) -> None:
        app_path = Path(__file__).resolve().parents[2] / "src" / "ui" / "app.py"
        with patch("src.retrieval.retrieve.read_setting", return_value=None):
            app = AppTest.from_file(str(app_path)).run()
            app.button[0].click().run()

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Enter a question" in element.value for element in app.warning))


if __name__ == "__main__":
    unittest.main()
