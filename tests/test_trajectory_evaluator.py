"""Scoring what the agent did, not what it said."""

from skill_lens.evaluators.trajectory import TrajectoryEvaluator
from skill_lens.models import CallArgsSpec, EvalCase, RunResult, ToolCall, TrajectorySpec

EVALUATOR = TrajectoryEvaluator()


def case(**trajectory) -> EvalCase:
    return EvalCase(name="c", task="t", trajectory=TrajectorySpec(**trajectory))


def result(*names: str) -> RunResult:
    return RunResult(tool_calls=[ToolCall(name=name) for name in names])


def test_no_trajectory_block_is_a_vacuous_pass():
    score = EVALUATOR.evaluate(EvalCase(name="c", task="t"), result("anything"))
    assert score.passed is True
    assert score.score == 1.0
    assert "no trajectory checks" in score.detail


def test_called_passes_when_every_listed_tool_was_used():
    score = EVALUATOR.evaluate(case(called=["lookup_order"]), result("lookup_order"))
    assert score.passed is True


def test_called_fails_when_a_listed_tool_was_never_used():
    score = EVALUATOR.evaluate(case(called=["lookup_order"]), result("issue_refund"))
    assert score.passed is False
    assert "lookup_order" in score.detail


def test_forbidden_fails_when_a_banned_tool_was_used():
    score = EVALUATOR.evaluate(case(forbidden=["issue_refund"]), result("issue_refund"))
    assert score.passed is False
    assert "issue_refund" in score.detail


def test_forbidden_passes_when_the_banned_tool_was_avoided():
    assert EVALUATOR.evaluate(case(forbidden=["issue_refund"]), result("lookup_order")).passed


def test_order_allows_unrelated_calls_in_between():
    # "Order" means relative subsequence: a model taking a sensible extra step
    # between the two required ones has still done them in the right order.
    score = EVALUATOR.evaluate(
        case(order=["lookup_order", "issue_refund"]),
        result("lookup_order", "check_policy", "issue_refund"),
    )
    assert score.passed is True


def test_order_fails_when_the_sequence_is_inverted():
    score = EVALUATOR.evaluate(
        case(order=["lookup_order", "issue_refund"]),
        result("issue_refund", "lookup_order"),
    )
    assert score.passed is False
    assert "order" in score.detail


def test_order_fails_when_a_listed_tool_is_missing_entirely():
    score = EVALUATOR.evaluate(case(order=["lookup_order", "issue_refund"]), result("lookup_order"))
    assert score.passed is False


def test_max_calls_catches_a_loop():
    score = EVALUATOR.evaluate(case(max_calls=2), result("a", "a", "a"))
    assert score.passed is False
    assert "3" in score.detail


def test_max_calls_passes_at_the_limit():
    assert EVALUATOR.evaluate(case(max_calls=2), result("a", "a")).passed is True


def test_score_is_the_fraction_of_checks_that_held():
    # called (fails) + forbidden (holds) => 1 of 2.
    score = EVALUATOR.evaluate(
        case(called=["lookup_order"], forbidden=["issue_refund"]), result("check_policy")
    )
    assert score.score == 0.5
    assert score.passed is False


def test_evaluator_reports_its_name():
    assert EVALUATOR.evaluate(case(called=["a"]), result("a")).evaluator == "trajectory"


def triggering_case(expected: bool) -> EvalCase:
    return EvalCase(
        name="c",
        task="t",
        mode="offered",
        trajectory=TrajectorySpec(skill_triggered=expected),
    )


def test_a_skill_that_triggered_when_it_should_have_passes():
    score = TrajectoryEvaluator().evaluate(triggering_case(True), RunResult(skill_triggered=True))
    assert score.passed is True
    assert score.errored is False


def test_a_skill_that_did_not_trigger_when_it_should_have_fails():
    score = TrajectoryEvaluator().evaluate(triggering_case(True), RunResult(skill_triggered=False))
    assert score.passed is False
    assert score.errored is False
    assert "not triggered" in score.detail


def test_a_negative_control_fails_when_the_skill_fires_anyway():
    # A positives-only suite scores a skill that fires on everything at 100%.
    score = TrajectoryEvaluator().evaluate(triggering_case(False), RunResult(skill_triggered=True))
    assert score.passed is False
    assert "expected False" in score.detail


def test_a_negative_control_passes_when_the_skill_stays_out_of_it():
    score = TrajectoryEvaluator().evaluate(triggering_case(False), RunResult(skill_triggered=False))
    assert score.passed is True


def test_a_runner_that_reported_no_decision_errors_rather_than_failing():
    # None is "this runner does not do offered mode", which is infra, not a
    # signal about the skill -- it must not read as a skill that misfired.
    score = TrajectoryEvaluator().evaluate(triggering_case(True), RunResult(skill_triggered=None))
    assert score.errored is True
    assert score.passed is False
    assert "does not support" in score.detail


def test_a_negative_control_on_an_unsupported_runner_errors_too():
    # A truthy guard would misreport this as a plain failure (did not trigger
    # when expected), not an infra error (runner does not support offered mode).
    score = TrajectoryEvaluator().evaluate(triggering_case(False), RunResult(skill_triggered=None))
    assert score.errored is True
    assert score.passed is False
    assert "does not support" in score.detail


def test_the_triggering_check_counts_toward_the_score_fraction():
    case = EvalCase(
        name="c",
        task="t",
        mode="offered",
        trajectory=TrajectorySpec(max_calls=5, skill_triggered=True),
    )
    score = TrajectoryEvaluator().evaluate(case, RunResult(skill_triggered=False))
    assert score.score == 0.5


def test_each_declared_tool_gets_its_own_check():
    case = EvalCase(
        name="c",
        task="t",
        trajectory=TrajectorySpec(called=["lookup_order", "issue_refund"]),
    )
    result = RunResult(tool_calls=[ToolCall(name="lookup_order")])
    score = TrajectoryEvaluator().evaluate(case, result)

    assert [(c.id, c.passed) for c in score.checks] == [
        ("called:lookup_order", True),
        ("called:issue_refund", False),
    ]


def test_order_and_max_calls_are_single_checks():
    case = EvalCase(
        name="c",
        task="t",
        trajectory=TrajectorySpec(order=["a", "b"], max_calls=1),
    )
    result = RunResult(tool_calls=[ToolCall(name="b"), ToolCall(name="a")])
    score = TrajectoryEvaluator().evaluate(case, result)

    assert [(c.id, c.passed) for c in score.checks] == [("order", False), ("max_calls", False)]


def test_the_triggering_check_has_an_id_of_its_own():
    case = EvalCase(
        name="c", task="t", mode="offered", trajectory=TrajectorySpec(skill_triggered=True)
    )
    score = TrajectoryEvaluator().evaluate(case, RunResult(skill_triggered=True))
    assert [c.id for c in score.checks] == ["skill_triggered"]


def test_an_errored_trajectory_reports_no_checks():
    # The runner reported no triggering decision at all. There is no verdict to
    # record, and a check here would read as a real one.
    case = EvalCase(
        name="c", task="t", mode="offered", trajectory=TrajectorySpec(skill_triggered=True)
    )
    score = TrajectoryEvaluator().evaluate(case, RunResult(skill_triggered=None))
    assert score.errored is True
    assert score.checks == []


# --- call_args: what a tool was called *with* ------------------------------


def args_case(*entries: CallArgsSpec) -> EvalCase:
    return EvalCase(name="c", task="t", trajectory=TrajectorySpec(call_args=list(entries)))


def calls(*pairs: tuple[str, dict]) -> RunResult:
    return RunResult(tool_calls=[ToolCall(name=n, arguments=a) for n, a in pairs])


def test_contains_passes_when_one_call_carried_the_arguments():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="list_threads", contains={"status": "active"})),
        calls(("list_threads", {"status": "active", "limit": 50})),
    )
    assert score.passed is True
    assert [(c.id, c.passed) for c in score.checks] == [("call_args[0]", True)]
    assert "call 1 of 1 to list_threads matched" in score.checks[0].evidence


def test_contains_fails_when_the_tool_was_never_called():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="list_threads", contains={"status": "active"})),
        calls(("other", {"status": "active"})),
    )
    assert score.passed is False
    assert score.checks[0].evidence == "list_threads was never called"


def test_contains_fails_when_no_call_matched_and_shows_what_was_sent():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="list_threads", contains={"status": "active"})),
        calls(("list_threads", {"status": "all"}), ("list_threads", {})),
    )
    assert score.passed is False
    evidence = score.checks[0].evidence
    assert "no call to list_threads matched contains" in evidence
    assert '{"status": "active"}' in evidence
    assert '{"status": "all"}' in evidence
    assert "{}" in evidence


def test_any_call_matching_is_enough_by_default():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="list_threads", contains={"status": "active"})),
        calls(("list_threads", {"status": "all"}), ("list_threads", {"status": "active"})),
    )
    assert score.passed is True
    assert "call 2 of 2 to list_threads matched" in score.checks[0].evidence


def test_contains_is_a_subset_at_every_level():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="search", contains={"query": {"status": "active"}})),
        calls(("search", {"query": {"status": "active", "limit": 10}, "page": 1})),
    )
    assert score.passed is True


def test_contains_fails_when_a_nested_key_is_missing():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="search", contains={"query": {"status": "active"}})),
        calls(("search", {"query": {"limit": 10}})),
    )
    assert score.passed is False


def test_lists_match_element_by_element_at_the_same_length():
    held = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="tag", contains={"items": [{"id": 1}]})),
        calls(("tag", {"items": [{"id": 1, "qty": 2}]})),
    )
    assert held.passed is True
    longer = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="tag", contains={"labels": ["bug"]})),
        calls(("tag", {"labels": ["bug", "urgent"]})),
    )
    assert longer.passed is False


def test_a_boolean_never_equals_a_number():
    # Python says True == 1. An author who wrote `limit: 1` must not pass on a
    # call that sent `true`, in either direction.
    as_bool = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="t", contains={"limit": 1})), calls(("t", {"limit": True}))
    )
    as_int = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="t", contains={"flag": True})), calls(("t", {"flag": 1}))
    )
    assert as_bool.passed is False
    assert as_int.passed is False


def test_a_string_never_equals_a_number():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="t", contains={"id": "1"})), calls(("t", {"id": 1}))
    )
    assert score.passed is False


def test_equals_requires_the_whole_argument_dict():
    exact = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="reply", equals={"thread_id": 42, "body_file": "r.md"})),
        calls(("reply", {"thread_id": 42, "body_file": "r.md"})),
    )
    assert exact.passed is True
    extra = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="reply", equals={"thread_id": 42})),
        calls(("reply", {"thread_id": 42, "body_file": "r.md"})),
    )
    assert extra.passed is False
    assert "no call to reply matched equals" in extra.checks[0].evidence


def test_an_empty_equals_means_called_with_no_arguments():
    bare = EVALUATOR.evaluate(args_case(CallArgsSpec(tool="ping", equals={})), calls(("ping", {})))
    assert bare.passed is True
    loaded = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="ping", equals={})), calls(("ping", {"x": 1}))
    )
    assert loaded.passed is False


def test_every_requires_each_call_to_match():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="list_threads", contains={"status": "active"}, every=True)),
        calls(("list_threads", {"status": "all"}), ("list_threads", {"status": "active"})),
    )
    assert score.passed is False
    assert "call 1 of 2 to list_threads did not match contains" in score.checks[0].evidence
    assert '{"status": "all"}' in score.checks[0].evidence


def test_every_passes_when_all_calls_match():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="list_threads", contains={"status": "active"}, every=True)),
        calls(
            ("list_threads", {"status": "active"}),
            ("other", {}),
            ("list_threads", {"status": "active", "page": 2}),
        ),
    )
    assert score.passed is True
    assert score.checks[0].evidence == (
        'all 2 calls to list_threads matched contains {"status": "active"}'
    )


def test_every_still_fails_when_the_tool_was_never_called():
    # "Every call matched" over zero calls would be a vacuous pass.
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="list_threads", contains={"status": "active"}, every=True)),
        calls(("other", {})),
    )
    assert score.passed is False
    assert score.checks[0].evidence == "list_threads was never called"


def test_call_args_ids_are_positional_so_two_entries_may_name_one_tool():
    score = EVALUATOR.evaluate(
        args_case(
            CallArgsSpec(tool="t", contains={"a": 1}),
            CallArgsSpec(tool="t", contains={"a": 2}),
        ),
        calls(("t", {"a": 1})),
    )
    assert [(c.id, c.passed) for c in score.checks] == [
        ("call_args[0]", True),
        ("call_args[1]", False),
    ]
    assert score.score == 0.5


def test_call_args_checks_come_after_the_other_trajectory_checks():
    case = EvalCase(
        name="c",
        task="t",
        trajectory=TrajectorySpec(
            called=["t"], max_calls=5, call_args=[CallArgsSpec(tool="t", contains={"a": 1})]
        ),
    )
    score = EVALUATOR.evaluate(case, calls(("t", {"a": 1})))
    assert [c.id for c in score.checks] == ["called:t", "max_calls", "call_args[0]"]


def test_unparseable_arguments_fail_and_show_the_raw_payload():
    # Runners keep a payload they could not parse under `_raw`. It can never
    # match a structural check, and the evidence must show what was captured.
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="t", contains={"a": 1})),
        calls(("t", {"_raw": "{not json"})),
    )
    assert score.passed is False
    assert '"_raw": "{not json"' in score.checks[0].evidence


def test_long_arguments_in_evidence_are_cut_with_a_visible_count():
    score = EVALUATOR.evaluate(
        args_case(CallArgsSpec(tool="write", contains={"path": "a.md"})),
        calls(("write", {"path": "b.md", "content": "x" * 5000})),
    )
    evidence = score.checks[0].evidence
    assert len(evidence) < 1500
    assert "more characters)" in evidence
