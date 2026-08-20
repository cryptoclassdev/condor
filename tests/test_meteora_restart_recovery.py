from condor.agents.strategy import StrategyStore


def test_meteora_operator_opts_into_restart_recovery():
    strategy = StrategyStore().get("meteora_regime_lp", "regime_lp_operator")

    assert strategy is not None
    assert strategy.default_config["restart_on_boot"] is True
