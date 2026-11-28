"""The emoji catalog shared by the picker (client) and reaction validation (server).

Reactions are accepted only if the emoji is in this catalog, so arbitrary text
can never be stored or broadcast as a "reaction".
"""
import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent.parent / "static" / "data" / "emoji.json"


@lru_cache(maxsize=1)
def allowed_emoji() -> frozenset[str]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return frozenset(
        emoji for group in catalog["groups"] for emoji, _name in group["emojis"]
    )


def is_valid_emoji(value) -> bool:
    return isinstance(value, str) and value in allowed_emoji()
