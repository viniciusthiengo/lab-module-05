import pytest

from app.agents.protocol import Decision, Delegation, ProtocolError, agent_key, parse_decision

AGENTS = ["researcher", "writer", "reviewer", "fact_checker"]


def parse(text: str) -> Decision:
    return parse_decision(text, AGENTS)


class TestDelegation:
    def test_one_delegation(self) -> None:
        decision = parse(
            "REASON: We need facts first.\n"
            "DELEGATE: researcher\n"
            "TASK: Find the main types of batteries.\n"
            "CONTEXT: none"
        )

        assert decision.kind == "delegate"
        assert decision.reason == "We need facts first."
        assert decision.delegations == (
            Delegation("researcher", "Find the main types of batteries.", ()),
        )

    def test_several_delegations_run_together(self) -> None:
        decision = parse(
            "REASON: Two independent topics.\n"
            "DELEGATE: researcher\nTASK: Lithium-ion.\nCONTEXT: all\n"
            "DELEGATE: researcher\nTASK: Solid-state.\n"
        )

        assert [(d.agent, d.task, d.context) for d in decision.delegations] == [
            ("researcher", "Lithium-ion.", None),
            ("researcher", "Solid-state.", None),
        ]

    def test_multi_line_tasks_and_step_numbers(self) -> None:
        decision = parse(
            "DELEGATE: writer\n"
            "TASK: Write a short article.\n"
            "- 300 words\n"
            "- for beginners\n"
            "CONTEXT: steps 1, 3 and 1"
        )

        delegation = decision.delegations[0]
        assert delegation.task == "Write a short article.\n- 300 words\n- for beginners"
        assert delegation.context == (1, 3)
        assert decision.reason == ""

    @pytest.mark.parametrize(
        "text",
        [
            "**DELEGATE:** Researcher\n**TASK:** Find facts.",
            "- delegate: researcher\n- task: Find facts.",
            "DELEGATE : `researcher`\nTASK:   Find facts.  ",
        ],
    )
    def test_markdown_and_spacing_around_keywords(self, text: str) -> None:
        assert parse(text).delegations == (Delegation("researcher", "Find facts."),)

    def test_worker_names_are_normalized(self) -> None:
        assert agent_key("Fact-Checker") == "fact_checker"
        assert agent_key(" fact checker ") == "fact_checker"
        assert parse("DELEGATE: Fact-checker\nTASK: Check it.").delegations[0].agent == (
            "fact_checker"
        )


class TestFinal:
    def test_final_takes_everything_after_it(self) -> None:
        decision = parse(
            "REASON: The draft passed the review.\n"
            "FINAL:\n"
            "# Batteries\n\n"
            "TASK: this line is part of the result.\n"
            "DELEGATE: so is this one."
        )

        assert decision.kind == "final"
        assert decision.final == (
            "# Batteries\n\nTASK: this line is part of the result.\nDELEGATE: so is this one."
        )
        assert decision.delegations == ()

    def test_final_on_the_same_line(self) -> None:
        assert parse("FINAL: Paris is the capital of France.").final == (
            "Paris is the capital of France."
        )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("I think we should research first.", "Start with DELEGATE"),
        ("", "Start with DELEGATE"),
        ("DELEGATE: designer\nTASK: Draw it.", "There is no worker named 'designer'"),
        ("DELEGATE: writer", "has no TASK line"),
        ("TASK: Find facts.", "TASK must follow a DELEGATE line"),
        ("REASON: Done.\nFINAL:", "FINAL is empty"),
        ("DELEGATE: writer\nTASK: Write.\nFINAL: Done.", "not both"),
        (
            "\n".join(f"DELEGATE: researcher\nTASK: Topic {n}." for n in range(4)),
            "at most 3 delegations",
        ),
    ],
)
def test_answers_that_do_not_follow_the_format(text: str, message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        parse(text)
