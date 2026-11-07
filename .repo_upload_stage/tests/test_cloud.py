from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.cloud import GeminiClient, looks_like_web_request, requests_report_save


class GeminiClientTests(unittest.TestCase):
    @patch("app.cloud.requests.post")
    def test_web_search_uses_grounding_and_does_not_store_interaction(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "output_text": "The answer is grounded.",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": "The answer is grounded.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "title": "Official source",
                                    "url": "https://example.com/source",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        post.return_value = response

        result = GeminiClient(api_key="unit-test-key", model="gemini-3.8-flash").generate(
            "Search the web for a current example", web_search=True
        )

        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["model"], "gemini-3.8-flash")
        self.assertEqual(kwargs["json"]["tools"], [{"type": "google_search"}])
        self.assertIs(kwargs["json"]["store"], False)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "unit-test-key")
        self.assertEqual(result.sources, [("Official source", "https://example.com/source")])
        self.assertIn("https://example.com/source", result.text)

    @patch("app.cloud.requests.post")
    def test_can_extract_answer_from_model_output_steps(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "steps": [
                {"type": "model_output", "content": [{"type": "text", "text": "Step answer only."}]}
            ]
        }
        post.return_value = response

        result = GeminiClient(api_key="unit-test-key").generate("Explain this")
        self.assertEqual(result.text, "Step answer only.")

    @patch("app.cloud.requests.post")
    def test_api_error_is_reported_clearly(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 400
        response.json.return_value = {"error": {"message": "Unknown model"}}
        response.text = "bad request"
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, r"Gemini API error \(400\): Unknown model"):
            GeminiClient(api_key="unit-test-key").generate("hello")

    def test_missing_key_is_rejected_before_network_call(self) -> None:
        client = GeminiClient(api_key="")
        with patch("app.cloud.requests.post") as post:
            with self.assertRaisesRegex(RuntimeError, "API key is missing"):
                client.generate("hello")
            post.assert_not_called()


class RoutingTests(unittest.TestCase):
    def test_routes_explicit_current_web_requests(self) -> None:
        self.assertTrue(looks_like_web_request("Search the web for recent AI announcements"))
        self.assertTrue(looks_like_web_request("What are the latest local AI models?"))
        self.assertFalse(looks_like_web_request("Explain binary search to me"))

    def test_detects_report_requests(self) -> None:
        self.assertTrue(requests_report_save("Research this online and save the research as a file"))
        self.assertFalse(requests_report_save("Just tell me what this means"))


if __name__ == "__main__":
    unittest.main()
