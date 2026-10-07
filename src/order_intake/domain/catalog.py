"""Local catalog with the only lookup the model is allowed to use."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

STOPWORDS = {"the", "a", "an", "of", "for", "and", "or", "please", "send", "x", "individual", "usual", "some"}


def normalize_sku(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def tokens(text: str) -> set[str]:
    result = set()
    for raw in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()):
        word = raw.replace("-", "")
        if word in STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        result.add(word)
    return result


@dataclass(frozen=True)
class CatalogItem:
    sku: str
    name: str
    unit_cents: int

    def as_dict(self) -> dict:
        return {"sku": self.sku, "name": self.name, "unit_cents": self.unit_cents}


class Catalog:
    def __init__(self, items: list[CatalogItem]):
        self.items = items
        self._by_sku = {normalize_sku(i.sku): i for i in items}

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        rows = json.loads(path.read_text(encoding="utf-8"))
        return cls([CatalogItem(r["sku"], r["name"], int(r["unit_cents"])) for r in rows])

    def get(self, sku: str | None) -> CatalogItem | None:
        return self._by_sku.get(normalize_sku(sku)) if sku else None

    def sku_in_text(self, text: str) -> CatalogItem | None:
        for word in re.findall(r"[A-Za-z]+-?\d+", text):
            if self.get(word):
                return self.get(word)
        return None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """A SKU mentioned in the query wins; otherwise rank items by shared name tokens."""
        exact = self.sku_in_text(query)
        if exact:
            return [{**exact.as_dict(), "match": "sku", "score": None}]
        wanted = tokens(query)
        scored = []
        for item in self.items:
            score = len(wanted & tokens(item.name))
            if score:
                scored.append({**item.as_dict(), "match": "description", "score": score})
        scored.sort(key=lambda r: (-r["score"], r["sku"]))
        return scored[:limit]

    def description_candidates(self, phrase: str) -> list[str]:
        """SKUs tied for the best description match; more than one means the phrase is ambiguous."""
        exact = self.sku_in_text(phrase)
        if exact:
            return [exact.sku]
        results = self.search(phrase)
        if not results:
            return []
        best = results[0]["score"]
        return [r["sku"] for r in results if r["score"] == best]
