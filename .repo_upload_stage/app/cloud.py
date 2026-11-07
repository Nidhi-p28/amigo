from __future__ import annotations

import re
import requests
from dataclasses import dataclass
from typing import Any

from .config import GEMINI_API_KEY, GEMINI_MODEL

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


@dataclass
class CloudResult:
    text: str
    sources: list[tuple[str, str]]


class GeminiClient:
    """Gemini Interactions API client, optionally grounded by Google Search.

    Requests are stateless by default: store=False prevents the Interactions API
    from retaining the interaction for later conversation chaining.
    """

    def __init__(self, api_key: str = GEMINI_API_KEY, model: str = GEMINI_MODEL) -> None:
        self.api_key = api_key
        self.model = model

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, *, web_search: bool = False) -> CloudResult:
        if not self.configured:
            raise RuntimeError("Gemini API key is missing. Add GEMINI_API_KEY to your local .env file.")

        payload: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "store": False,
        }
        if web_search:
            payload["tools"] = [{"type": "google_search"}]

        try:
            response = requests.post(
                INTERACTIONS_URL,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=(10, 120),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Couldn't reach Gemini API: {exc}") from exc

        if response.status_code >= 400:
            try:
                body = response.json()
                message = body.get("error", {}).get("message", response.text[:400])
            except (ValueError, AttributeError):
                message = response.text[:400]
            raise RuntimeError(f"Gemini API error ({response.status_code}): {message}")

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Gemini returned a response that wasn't valid JSON.") from exc

        answer = str(data.get("output_text") or "").strip()
        sources: list[tuple[str, str]] = []
        seen: set[str] = set()
        output_text_blocks: list[str] = []

        # Grounding citations are attached to text blocks in model_output steps.
        for step in data.get("steps", []) or []:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            for block in step.get("content", []) or []:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                block_text = str(block.get("text") or "").strip()
                if block_text:
                    output_text_blocks.append(block_text)
                for annotation in block.get("annotations", []) or []:
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    url = str(annotation.get("url") or "").strip()
                    title = str(annotation.get("title") or "").strip() or url
                    if url.startswith(("https://", "http://")) and url not in seen:
                        sources.append((title, url))
                        seen.add(url)

        if not answer:
            answer = "\n\n".join(output_text_blocks).strip()
        if not answer:
            raise RuntimeError("Gemini returned no text. Try again or check the selected model in .env.")

        if sources:
            answer += "\n\nSources:\n" + "\n".join(f"- {title}: {url}" for title, url in sources)
        return CloudResult(text=answer, sources=sources)


def looks_like_web_request(text: str) -> bool:
    """Conservative keyword router; don't send ordinary chat to the cloud."""
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    cues = (
        "search the web", "search online", "look it up", "look up online", "look up the latest",
        "research online", "research the web", "research this online", "on the internet",
        "online sources", "browse the web", "web search", "search for recent", "find current",
        "latest", "current news", "recent news", "today's news", "this week's news",
        "recent developments", "what happened today", "as of today", "current information",
        "ai news", "tech news", "technology news", "industry news", "news roundup",
        "find news", "news about", "product launches", "tech announcements",
    )
    return any(cue in normalized for cue in cues)


def looks_like_deep_reasoning(text: str) -> bool:
    normalized = text.lower()
    cues = (
        "analyze in depth", "analyse in depth", "deep analysis", "compare in detail",
        "detailed strategic analysis", "think through this carefully",
    )
    return len(text) > 1200 or any(cue in normalized for cue in cues)


def requests_report_save(text: str) -> bool:
    normalized = text.lower()
    cues = (
        "save this", "save the result", "save the report", "save a report", "write a report",
        "create a report", "put it in a file", "save it to a file", "save the findings",
        "save the research", "save your findings",
    )
    return any(cue in normalized for cue in cues)
