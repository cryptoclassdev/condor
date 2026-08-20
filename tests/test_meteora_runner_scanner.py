from agents.meteora_regime_lp.routines.runner_scanner import summarize_no_candidates


def test_network_wide_other_venues_are_reach_not_a_binding_gate():
    summary = summarize_no_candidates(
        raw_count=320,
        stats={
            "not_meteora": 182,
            "age_unknown": 2,
            "age_young": 4,
            "age_old": 70,
            "vol": 50,
            "accel": 8,
            "tvl": 4,
            "not_sol": 0,
            "excluded": 0,
        },
    )

    assert "138 were Meteora" in summary
    assert "binding gate is **ageOld** (70/138" in summary
    assert "182 were other venues — structural" in summary
    assert "binding gate is **not_meteora**" not in summary
