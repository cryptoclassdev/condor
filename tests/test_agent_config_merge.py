from condor.agents.config import merge_config


def test_session_overrides_preserve_strategy_specific_risk_limits():
    configured = {
        "risk_profile": "guardian",
        "risk_limits": {
            "min_wallet_sol_reserve": 0.06,
            "max_open_slots": 3,
            "daily_loss_limit_pct": 6,
            "drawdown_killswitch_pct": 10,
        },
    }

    merged = merge_config(
        configured,
        {"risk_limits": {"max_open_executors": 2}},
    )

    assert merged["risk_profile"] == "guardian"
    assert merged["risk_limits"] == {
        "min_wallet_sol_reserve": 0.06,
        "max_open_slots": 3,
        "daily_loss_limit_pct": 6,
        "drawdown_killswitch_pct": 10,
        "max_open_executors": 2,
    }
