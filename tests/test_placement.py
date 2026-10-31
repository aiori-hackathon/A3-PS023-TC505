# CleanSlot SafeCore - Placement Engine Test Suite
# Tests host capacity evaluation using safe_free = min(mem_available, budget - reserved) - buffer.
# Verifies placement selects the eligible host with maximum safe_free.
# Validates inclusion of the evaluation list in responses and explainable 409 rejections.

from fastapi.testclient import TestClient
from controller.placement import compute_safe_free, evaluate_placement


def test_safe_free_formula_unit():
    """Verify safe_free computation under various available and reserved scenarios."""
    # budget=4096, buffer=256
    # available=3584, reserved=1024 -> min(3584, 4096-1024)=3072 -> 3072-256 = 2816
    assert (
        compute_safe_free(
            mem_available_mib=3584,
            reserved_mib=1024,
            budget_mib=4096,
            safety_buffer_mib=256,
        )
        == 2816
    )

    # When available memory is lower than budget - reserved:
    # available=2000, reserved=500 -> min(2000, 3596)=2000 -> 2000-256 = 1744
    assert (
        compute_safe_free(
            mem_available_mib=2000,
            reserved_mib=500,
            budget_mib=4096,
            safety_buffer_mib=256,
        )
        == 1744
    )


def test_placement_selects_host_with_most_safe_free(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Ensure controller places VM on worker with higher safe_free.
    Simulate worker-1 having less available memory, so worker-2 is chosen.
    """
    w1_state = setup_test_environment["worker_1_state"]
    w2_state = setup_test_environment["worker_2_state"]

    # Pre-occupy memory on worker-1 by spawning a mock process of 2000 MiB
    w1_state.spawn_process("vm-preexisting", 2, 2000, "admin")

    payload = {
        "name": "placement-test-vm",
        "owner_label": "analytics",
        "vcpu": 2,
        "memory_mib": 1024,
    }
    resp = client.post("/vms", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()

    # Worker-2 should be selected because worker-1 had 2000 MiB already allocated
    assert data["selected_host"] == "worker-2"
    assert data["host"] == "worker-2"

    # Verify evaluation list is present (Correction 2)
    eval_list = data["evaluation"]
    assert len(eval_list) == 2
    w1_eval = next(e for e in eval_list if e["host"] == "worker-1")
    w2_eval = next(e for e in eval_list if e["host"] == "worker-2")

    assert w2_eval["safe_free"] > w1_eval["safe_free"]
    assert w2_eval["eligible"] is True
    assert "sufficient safe_free" in w2_eval["reason"].lower()


def test_placement_capacity_exhausted_returns_409(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Ensure 409 Conflict with evaluation list is returned when neither host fits.
    """
    w1_state = setup_test_environment["worker_1_state"]
    w2_state = setup_test_environment["worker_2_state"]

    # Occupy memory on both workers so 2048 cannot fit
    w1_state.spawn_process("vm-fill-1", 2, 2500, "admin")
    w2_state.spawn_process("vm-fill-2", 2, 2500, "admin")

    payload = {
        "name": "large-vm",
        "owner_label": "data",
        "vcpu": 2,
        "memory_mib": 2048,
    }
    resp = client.post("/vms", json=payload, headers=auth_headers)
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "INSUFFICIENT_CAPACITY"
    assert len(data["evaluation"]) == 2
    for host_eval in data["evaluation"]:
        assert host_eval["eligible"] is False
        assert "safe_free" in host_eval["reason"]
