import pytest

from app.model_replay import ReplaySuiteError, assert_cost_budget


def test_model_daily_budget_reserves_worst_case_call_cost() -> None:
    assert_cost_budget(0.65, 0.25, 1.0)
    with pytest.raises(ReplaySuiteError, match="预算不足"):
        assert_cost_budget(0.80, 0.25, 1.0)


def test_model_daily_budget_rejects_invalid_configuration() -> None:
    with pytest.raises(ReplaySuiteError, match="配置无效"):
        assert_cost_budget(0, 0.1, 0)
