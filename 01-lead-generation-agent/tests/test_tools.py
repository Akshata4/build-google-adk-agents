"""The count tools refuse to run out of order, whatever the model decides."""

from types import SimpleNamespace

from lead_generation_agent.tools import set_company_count, set_lead_count


def _ctx(**state):
    return SimpleNamespace(state=dict(state))


def test_company_count_needs_a_target():
    ctx = _ctx()
    assert set_company_count(3, ctx)["status"] == "error"
    assert "k" not in ctx.state


def test_company_count_advances_stage():
    ctx = _ctx(country="Thailand", industry="Fintech")
    assert set_company_count(3, ctx)["status"] == "ok"
    assert ctx.state["stage"] == "pattern_discovery"
    assert set_company_count(11, ctx)["status"] == "error"


def test_lead_count_needs_patterns():
    ctx = _ctx(country="Thailand", industry="Fintech")
    assert set_lead_count(1, ctx)["status"] == "error"
    assert "stage" not in ctx.state


def test_lead_count_advances_stage():
    ctx = _ctx(discovered_patterns="## Patterns ...")
    assert set_lead_count(5, ctx)["status"] == "ok"
    assert ctx.state == {
        "discovered_patterns": "## Patterns ...",
        "m": 5,
        "stage": "lead_generation",
    }
