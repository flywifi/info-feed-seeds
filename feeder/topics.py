"""The two-level topic registry (`schema/topics.json`)."""
from __future__ import annotations

from pathlib import Path

from . import ROOT, jsonio

TOPICS_FILE = ROOT / "schema" / "topics.json"


def load_topics(path: Path | None = None) -> dict:
    """Return the registry's `topics` map: topic id -> {subtopics, added, reason}."""
    data = jsonio.load_json(path or TOPICS_FILE)
    return data["topics"]


def all_tags(topics: dict | None = None) -> set[str]:
    """Every valid tag: each topic id and each topic/subtopic pair."""
    topics = topics if topics is not None else load_topics()
    tags: set[str] = set()
    for topic, entry in topics.items():
        tags.add(topic)
        for sub in entry.get("subtopics", []):
            tags.add(f"{topic}/{sub}")
    return tags


def is_valid_tag(tag: str, topics: dict | None = None) -> bool:
    return tag in all_tags(topics)


def split_tag(tag: str) -> tuple[str, str | None]:
    """'topic/subtopic' -> ('topic', 'subtopic'); 'topic' -> ('topic', None)."""
    topic, _, sub = tag.partition("/")
    return topic, (sub or None)


__all__ = ["TOPICS_FILE", "all_tags", "is_valid_tag", "load_topics", "split_tag"]
