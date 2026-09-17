"""Arabic text normalization for search matching and argument comparison.

Stdlib-only helpers shared by the gateway read path and the eval harness.
"""


def normalize_arabic(text: str) -> str:
    """Normalize Arabic text for search matching and argument comparison."""
    if not isinstance(text, str):
        return str(text)
    s = text.strip()
    s = "".join(c for c in s if c not in "ًٌٍَُِّْٰ")
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    s = s.replace("ة", "ه").replace("ى", "ي")
    return s.lower()
