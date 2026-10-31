# CleanSlot SafeCore - Chaos Loop Test Suite
# Tests 20-cycle deterministic churn across mock worker nodes.
# NOTE: Workers and PIDs are MOCKS simulated in-memory; no real OS processes run or are killed.
# Asserts 0 leaked fake processes, 0 leaked memory reservations, and exact capacity restoration.

from demo.chaos_lite import run_chaos_loop


def test_deterministic_chaos_loop_zero_leaks(setup_test_environment):
    """
    Execute 20 back-to-back chaos cycles alternating between workers and fault modes.
    Fails if any cycle leaks mock process entries or capacity reservations.
    """
    result = run_chaos_loop(num_cycles=20, output_file="docs/chaos_result.txt")

    assert result["cycles"] == 20
    assert result["leaked_processes"] == 0, f"Leaked {result['leaked_processes']} fake processes"
    assert result["leaked_mib"] == 0, f"Leaked {result['leaked_mib']} MiB of reservation"
    assert len(result["records"]) == 20

    for r in result["records"]:
        assert r["leaked_procs"] == 0
        assert r["leaked_mib"] == 0
        assert r["receipt_checks"] == "All 4 True"
