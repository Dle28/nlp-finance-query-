from __future__ import annotations

from finance_query import cli


def test_evaluate_blocked_command_has_explicit_prepare_mode() -> None:
    args = cli.parse_args(
        [
            "evaluate-blocked",
            "--submission-dir",
            "artifacts/submission",
            "--output-dir",
            "artifacts/feedback",
            "--feedback-prepare-only",
        ]
    )
    assert args.command == "evaluate-blocked"
    assert args.feedback_prepare_only is True
    assert args.feedback_prompt_profile == "en_system_vi_context_compact_v2"


def test_unified_flow_keeps_builder_and_feedback_options() -> None:
    args = cli.parse_args(
        [
            "run-submission-flow",
            "--output",
            "submissions/example",
            "--feedback-model-name",
            "local/critic",
            "--feedback-max-questions",
            "3",
        ]
    )
    assert args.command == "run-submission-flow"
    assert str(args.output) == "submissions/example"
    assert args.feedback_model_name == "local/critic"
    assert args.feedback_max_questions == 3


def test_unified_flow_requires_explicit_candidate_only_opt_out() -> None:
    args = cli.parse_args(
        [
            "run-submission-flow",
            "--output",
            "submissions/example",
            "--skip-e2e",
        ]
    )
    assert args.skip_e2e is True
