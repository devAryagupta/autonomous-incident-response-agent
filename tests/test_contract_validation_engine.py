from incident_agent.contracts import FixAction, FixActionType, FixPlan, RiskLevel
from incident_agent.validation import validate_plan


def test_validate_plan_passes_for_supported_action() -> None:
    plan = FixPlan(
        hypothesis_id="h1",
        risk=RiskLevel.LOW,
        actions=[
            FixAction(
                action_type=FixActionType.ROLLBACK_DEPLOYMENT,
                target="deployment/demo-app",
                params={},
                rationale="rollback supported",
            )
        ],
    )
    verdict = validate_plan(fix_plan=plan)
    assert verdict.passed is True


def test_validate_plan_fails_for_missing_target() -> None:
    plan = FixPlan(
        hypothesis_id="h1",
        risk=RiskLevel.LOW,
        actions=[
            FixAction(
                action_type=FixActionType.NOOP,
                target="",
                params={},
                rationale="noop",
            )
        ],
    )
    verdict = validate_plan(fix_plan=plan)
    assert verdict.passed is False

