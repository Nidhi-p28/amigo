"""Small pure-Python helpers for local wake-word detection."""

import re


_WAKE_WORD_PATTERN = re.compile(r"(?<![a-z])amigo(?![a-z])|(?<![a-z])a\s+me\s+go(?![a-z])", re.IGNORECASE)
_WAKE_PHRASE_PATTERN = re.compile(
    r"(?<![a-z])hey\s+amigo(?![a-z])|(?<![a-z])amigo(?![a-z])|(?<![a-z])a\s+me\s+go(?![a-z])",
    re.IGNORECASE,
)


def contains_wake_word(text: str) -> bool:
    """Match Amigo as a word, not as part of a longer word such as 'amigos'."""
    normalized = re.sub(r"[^a-z\s]", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return bool(_WAKE_WORD_PATTERN.search(normalized))


def command_after_wake_word(text: str) -> str:
    """Return any command spoken in the same utterance after the wake phrase."""
    command = _WAKE_PHRASE_PATTERN.sub(" ", text, count=1)
    return re.sub(r"\s+", " ", command).strip(" \t,.:;!?-")
