"""GORGONA-owned text embedder (no third-party weights).

Feature hashing over word unigrams and character trigrams, signed, sublinear, L2-normalised.
Deterministic and fully reproducible from this code, so initialization, weights and
licensing are GORGONA-controlled. A learned embedder can replace it behind the same
interface; its version string then changes and every embedding is refreshed.
"""

import hashlib
import math
import re
import unicodedata
from typing import Protocol

DIMENSIONS = 256
_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    version: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    version = f"gorgona-hash-{DIMENSIONS}-v1"
    dimensions = DIMENSIONS

    def embed(self, text: str) -> list[float]:
        counts: dict[int, float] = {}
        for feature in _features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % DIMENSIONS
            sign = 1.0 if (value >> 63) & 1 else -1.0
            counts[index] = counts.get(index, 0.0) + sign
        vector = [0.0] * DIMENSIONS
        for index, raw in counts.items():
            vector[index] = math.copysign(math.log1p(abs(raw)), raw)
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm > 0 else vector


def _features(text: str) -> list[str]:
    words = _WORD.findall(unicodedata.normalize("NFKC", text).casefold())
    features = [f"w:{w}" for w in words]
    for word in words:
        padded = f"<{word}>"
        features.extend(f"c:{padded[i : i + 3]}" for i in range(len(padded) - 2))
    return features


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))
