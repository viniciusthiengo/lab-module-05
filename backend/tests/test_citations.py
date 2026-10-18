import pytest

from app.citations import SourceList, markers, renumber, split_sources, strip_citations
from app.llm.base import Source

A = Source("https://a.org/x", "Page A")
B = Source("https://b.org/y", "Page B")
C = Source("https://c.org/z", "Page C")


class TestMarkers:
    def test_markers_are_found_in_order_of_first_appearance(self) -> None:
        assert markers("One [3]. Two [1][3]. Three [2, 7].") == [3, 1, 2, 7]

    @pytest.mark.parametrize(
        "text",
        ["items[3] = 0", "a link [3](https://a.org)", r"escaped \[3]", "a box [ ] and [x]"],
    )
    def test_indexes_links_and_other_brackets_are_not_markers(self, text: str) -> None:
        assert markers(text) == []

    def test_renumbering_maps_drops_and_splits_markers(self) -> None:
        mapping = {1: 4, 2: 4, 3: 9}.get

        assert renumber("A [1]. B [2, 3]. C [5]. D [5][1].", mapping) == (
            "A [4]. B [4][9]. C. D [4]."
        )


class TestSourceLists:
    @pytest.mark.parametrize(
        "heading",
        ["Sources:", "## Sources", "### References", "**Sources:**", "**Fontes**", "SOURCES"],
    )
    def test_a_list_of_sources_at_the_end_is_split_off(self, heading: str) -> None:
        text = f"# Report\n\nText [1].\n\n---\n\n{heading}\n1. Page A — https://a.org/x\n- [2] B"

        body, word = split_sources(text)

        assert body == "# Report\n\nText [1]."
        assert word is not None
        assert word.lower() in heading.lower()

    def test_a_heading_followed_by_more_than_a_list_is_content(self) -> None:
        text = "## Sources\n- Lithium comes from brine.\n\nMore prose about it follows here."

        assert split_sources(text) == (text, None)

    def test_text_without_a_list_is_kept_whole(self) -> None:
        assert split_sources("Just text [1].") == ("Just text [1].", None)

    def test_stripping_citations_leaves_the_text_only(self) -> None:
        text = "Solid-state cells are safer [1][2].\n\nSources:\n- [1] A — https://a.org/x"

        assert strip_citations(text) == "Solid-state cells are safer."


class TestRunNumbers:
    def test_citations_get_one_number_per_page_for_the_whole_run(self) -> None:
        sources = SourceList()

        first = sources.cite("Fact one. [cite:1] Fact two. [cite:2]", (A, B), shown=())
        second = sources.cite("Fact three. [cite:1] Fact four. [cite:2]", (C, A), shown=())

        assert sources.sources == (A, B, C)
        assert first == (
            "Fact one. [1] Fact two. [2]\n\n"
            "Sources:\n- [1] Page A — https://a.org/x\n- [2] Page B — https://b.org/y"
        )
        assert second == (
            "Fact three. [3] Fact four. [1]\n\n"
            "Sources:\n- [1] Page A — https://a.org/x\n- [3] Page C — https://c.org/z"
        )

    def test_a_worker_keeps_only_the_markers_it_was_shown(self) -> None:
        sources = SourceList()
        sources.cite("Notes. [cite:1] [cite:2]", (A, B), shown=())

        draft = sources.cite(
            "Seen [2]. Invented [7]. Not shown [1].\n\n"
            "## Sources\n1. Invented page — https://invented.example/x",
            (),
            shown={2},
        )

        assert draft == "Seen [2]. Invented. Not shown.\n\nSources:\n- [2] Page B — https://b.org/y"

    def test_a_result_without_citations_stays_as_it_is(self) -> None:
        assert SourceList().cite("VERDICT: APPROVE", (), shown=()) == "VERDICT: APPROVE"


class TestFinalResult:
    def test_the_final_list_is_built_from_the_pages_cited_in_order_of_appearance(self) -> None:
        sources = SourceList()
        sources.cite("x [cite:1] [cite:2] [cite:3]", (A, B, C), shown=())

        final = sources.final(
            "# Result\n\nFirst [3]. Then [1, 3]. Unknown [9].\n\n"
            "**Fontes:**\n1. A page no search returned — https://invented.example/y"
        )

        assert final == (
            "# Result\n\nFirst [1]. Then [2][1]. Unknown.\n\n"
            "## Fontes\n\n1. Page C — https://c.org/z\n2. Page A — https://a.org/x"
        )

    def test_a_final_result_without_markers_gets_no_list(self) -> None:
        sources = SourceList()
        sources.cite("x [cite:1]", (A,), shown=())

        assert sources.final("# Result\n\nNo citations.") == "# Result\n\nNo citations."

    def test_a_title_missing_or_on_several_lines_is_tidied(self) -> None:
        untitled = Source("https://u.org", "https://u.org")
        sources = SourceList()
        sources.cite("x [cite:1] [cite:2]", (untitled, Source("https://v.org", "Two\n lines")), ())

        assert sources.final("See [1] and [2].").endswith(
            "1. https://u.org\n2. Two lines — https://v.org"
        )
