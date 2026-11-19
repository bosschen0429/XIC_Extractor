"""User guide prose names each concept with the term its glossary chose.

`docs/user/concepts.md#詞彙` owns one term per concept. Bold UI labels and code
spans quote the screen or files verbatim, so only the remaining prose is checked.
"""

import re

import pytest

from xic_workbench.user_guide import GUIDE_PAGES, SOURCE_ROOT

# Synonym → the glossary term that replaces it in prose.
SYNONYMS = {
    "群組": "feature",
    "跨樣本對應": "對齊",
    "上機序列表": "上機資訊",
    "輸出資料夾": "結果資料夾",
}
# The first-mention gloss that ties feature to the screen's 矩陣群組.
GLOSSES = ("（跨樣本群組）",)


def _prose(markdown: str) -> str:
    text = re.sub(r"```.*?```", "", markdown, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\*\*[^*]*\*\*", "", text)
    for gloss in GLOSSES:
        text = text.replace(gloss, "")
    return text


def synonym_lines(markdown: str) -> list[str]:
    return [
        f"{synonym} → {term}: {line.strip()}"
        for line in markdown.splitlines()
        for synonym, term in SYNONYMS.items()
        if synonym in _prose(line)
    ]


@pytest.mark.parametrize("page", [name for name, _ in GUIDE_PAGES])
def test_guide_prose_uses_glossary_terms(page):
    text = (SOURCE_ROOT / "docs/user" / page).read_text(encoding="utf-8")
    assert not synonym_lines(text)


def test_ui_labels_and_code_may_quote_the_screen():
    assert not synonym_lines("按 **瀏覽矩陣群組**，看 `group_center_mz`。")
    assert synonym_lines("每個群組一列") == ["群組 → feature: 每個群組一列"]
