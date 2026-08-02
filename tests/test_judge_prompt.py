"""The judge prompt: a pure function, so it is tested as one."""

from skill_eval.judges.prompt import SYSTEM_PROMPT, render_request
from skill_eval.models import JudgeRequest, RubricCheck


def request(**kwargs) -> JudgeRequest:
    kwargs.setdefault("task", "Why can't I return this?")
    kwargs.setdefault("output", "The return window is 30 days.")
    kwargs.setdefault("checks", [RubricCheck(id="r1", text="states the 30-day window")])
    return JudgeRequest(**kwargs)


def test_the_system_prompt_demands_evidence_and_forbids_inventing_ids():
    assert "evidence" in SYSTEM_PROMPT
    assert "id" in SYSTEM_PROMPT


def test_the_rendered_request_carries_task_output_and_every_check():
    text = render_request(
        request(
            checks=[
                RubricCheck(id="r1", text="states the 30-day window"),
                RubricCheck(id="r2", text="avoids jargon"),
            ]
        )
    )
    assert "Why can't I return this?" in text
    assert "The return window is 30 days." in text
    assert "r1: states the 30-day window" in text
    assert "r2: avoids jargon" in text


def test_the_expected_section_is_omitted_when_not_given():
    assert "good response" not in render_request(request())
    assert "good response" in render_request(request(expected="a plain explanation"))


def test_an_empty_output_is_labelled_rather_than_left_blank():
    # A blank section reads as a formatting glitch; the judge must be able to
    # tell "said nothing" apart from "the prompt lost the answer".
    assert "no output" in render_request(request(output=""))
