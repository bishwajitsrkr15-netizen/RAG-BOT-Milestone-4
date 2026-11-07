import unittest

from src.guardrails.query_guard import Intent, inspect_question


class InspectQuestionTests(unittest.TestCase):
    def test_detects_personal_identifiers(self) -> None:
        examples = [
            "My PAN is ABCDE1234F",
            "Aadhaar: 1234 5678 9012",
            "My email is person@example.com",
            "Call me at +91 9876543210",
            "Account number 123456789012",
            "OTP is 123456",
        ]
        for question in examples:
            with self.subTest(question=question):
                self.assertEqual(inspect_question(question).intent, Intent.PERSONAL_DATA)

    def test_detects_advice_and_performance_intents(self) -> None:
        self.assertEqual(
            inspect_question("Should I buy HDFC Large Cap Fund?").intent,
            Intent.ADVICE,
        )
        self.assertEqual(
            inspect_question("Compare the returns of these funds").intent,
            Intent.PERFORMANCE,
        )

    def test_allows_supported_factual_questions(self) -> None:
        questions = [
            "What is the expense ratio of HDFC Large Cap Fund?",
            "What is the exit load for HDFC Flexi Cap?",
            "What is the ELSS lock-in period?",
        ]
        for question in questions:
            with self.subTest(question=question):
                self.assertEqual(inspect_question(question).intent, Intent.FACTUAL)


if __name__ == "__main__":
    unittest.main()
