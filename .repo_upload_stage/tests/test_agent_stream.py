from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.agent import DesktopAgent


class StreamingTests(unittest.TestCase):
    @patch("app.agent.requests.post")
    def test_local_response_streams_chunks_and_assembles_answer(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            b'{"message":{"role":"assistant","content":"Hello"},"done":false}',
            b'{"message":{"role":"assistant","content":" there."},"done":true}',
        ]
        post.return_value = response
        chunks: list[str] = []

        result = DesktopAgent()._ollama_chat(
            [{"role": "user", "content": "hi"}], with_tools=False, on_chunk=chunks.append
        )

        self.assertEqual(chunks, ["Hello", " there."])
        self.assertEqual(result["message"]["content"], "Hello there.")
        kwargs = post.call_args.kwargs
        self.assertTrue(kwargs["stream"])
        self.assertTrue(kwargs["json"]["stream"])


if __name__ == "__main__":
    unittest.main()
