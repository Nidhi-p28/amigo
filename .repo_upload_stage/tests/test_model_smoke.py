"""Safe model smoke test: tool calls are inspected, never executed.

Run from the project root after installing requests and downloading the model:
    python tests/test_model_smoke.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL  # noqa: E402
from app.tools import tool_schemas  # noqa: E402


def main() -> int:
    print(f"Testing model: {OLLAMA_MODEL}")
    try:
        tags_response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=4)
        tags_response.raise_for_status()
        installed = [model.get("name", "") for model in tags_response.json().get("models", [])]
    except requests.RequestException as exc:
        print(f"FAIL: Ollama isn't reachable at {OLLAMA_BASE_URL}: {exc}")
        print("Start the Ollama app, then retry.")
        return 1

    if OLLAMA_MODEL not in installed:
        print("FAIL: model not found. Models available:")
        for name in installed:
            print(f"  - {name}")
        print(f"Run: ollama pull {OLLAMA_MODEL}")
        return 1
    print("PASS: Ollama is running and the model is installed.")

    try:
        chat = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": "Reply with exactly MODEL_OK and nothing else."}],
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=(5, 180),
        )
        chat.raise_for_status()
        content = chat.json().get("message", {}).get("content", "").strip()
        print(f"PASS: ordinary chat returned: {content[:200]!r}")
    except requests.RequestException as exc:
        print(f"FAIL: ordinary chat request failed: {exc}")
        return 1

    # Only ask the model to request a tool; this test does not launch apps or alter files.
    try:
        tool_response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": "Use the open_application tool to open Notepad. Do not just explain how."}],
                "tools": tool_schemas(),
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=(5, 180),
        )
        tool_response.raise_for_status()
        message = tool_response.json().get("message", {})
        calls = message.get("tool_calls") or []
        if calls:
            print("PASS: model generated a tool call (not executed):")
            print(json.dumps(calls, indent=2, ensure_ascii=False))
        else:
            print("WARNING: ordinary chat works, but this prompt didn't produce a tool call.")
            print("Try a different model or prompt. Amigo will be less reliable at desktop tasks until tool calling works.")
            print(f"Model response: {message.get('content', '')[:300]}")
    except requests.RequestException as exc:
        print(f"WARNING: the tool-call test failed: {exc}")
        print("Ordinary chat may still work, but Stage 2 tool use needs further checking.")

    print("\nSmoke test finished. Nothing was opened, created or deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
