"""The page: every id is used once (a second use makes the browser find the wrong element)."""

import re
from collections import Counter
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "src" / "spc" / "web" / "static" / "index.html").read_text(encoding="utf-8")


def test_every_id_of_the_page_is_unique():
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    assert [i for i, n in Counter(ids).items() if n > 1] == []
