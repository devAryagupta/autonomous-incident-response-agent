from incident_agent.contracts import FixAction, FixPlan
from incident_agent.validation import validate_plan


def test_validation_engine_interface() -> None:
    plan = FixPlan(
        hypothesis_id="h1-missing_secret",
        risk="low",
        actions=[
            FixAction(
                kind="patch_resource",
                target_ref="deployment/demo-app",
                params={},
                rationale="test",
            )
        ],
    )
    verdict = validate_plan(fix_plan=plan)
    assert verdict.passed is True
    assert verdict.reason

