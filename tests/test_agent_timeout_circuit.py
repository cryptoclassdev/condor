from condor.agents.timeout_circuit import TimeoutCircuit


def test_two_timeouts_enter_two_tick_degraded_mode_then_probe_again():
    circuit = TimeoutCircuit(threshold=2, degraded_ticks=2)

    assert circuit.should_attempt_model() is True
    circuit.record_timeout()
    assert circuit.should_attempt_model() is True
    circuit.record_timeout()

    assert circuit.should_attempt_model() is False
    assert circuit.should_attempt_model() is False
    assert circuit.should_attempt_model() is True


def test_success_fully_resets_timeout_circuit():
    circuit = TimeoutCircuit(threshold=2, degraded_ticks=3)
    circuit.record_timeout()
    circuit.record_success()
    circuit.record_timeout()

    assert circuit.should_attempt_model() is True
