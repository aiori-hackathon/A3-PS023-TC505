# CleanSlot SafeCore - Test Configuration and Fixtures
# Provides reusable fixtures for SQLite database isolation and mock worker setup.
# Configures test environment variables (CLEANSLOT_TOKEN) and in-memory ASGI transports.
# Ensures fast, deterministic testing without requiring real operating system sockets.

import os
import tempfile
import pytest
import httpx
from fastapi.testclient import TestClient

# Set test environment variables before importing applications
os.environ["CLEANSLOT_TOKEN"] = "test-cleanslot-token-123"
os.environ["WORKER_1_URL"] = "http://127.0.0.1:8101"
os.environ["WORKER_2_URL"] = "http://127.0.0.1:8102"

from controller.main import app as controller_app
from controller.database import init_db
from controller.worker_client import WorkerClient
import controller.main as controller_main

from worker.main import app as worker_app
from worker.state import MockWorkerState


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path):
    """Isolate SQLite database and worker state for each test."""
    db_file = tmp_path / "test_cleanslot.db"
    db_path = str(db_file)
    os.environ["CLEANSLOT_DB_PATH"] = db_path
    controller_main.DEFAULT_DB_PATH = db_path

    # Initialize fresh schema
    init_db(db_path)

    # Initialize isolated worker state instances
    w1_state = MockWorkerState(worker_id="worker-1", total_memory_mib=4096)
    w2_state = MockWorkerState(worker_id="worker-2", total_memory_mib=4096)

    # Attach ASGI transports for worker 1 and worker 2
    # Create distinct app factories/closures or assign states
    from copy import deepcopy

    class MockWorkerDispatch:
        def __init__(self, state: MockWorkerState):
            self.state = state

        async def __call__(self, scope, receive, send):
            # Temporarily bind worker_state for this call
            import worker.main as wm

            orig_state = wm.worker_state
            try:
                wm.worker_state = self.state
                await worker_app(scope, receive, send)
            finally:
                wm.worker_state = orig_state

    w1_dispatch = MockWorkerDispatch(w1_state)
    w2_dispatch = MockWorkerDispatch(w2_state)

    transports = {
        "http://127.0.0.1:8101": httpx.ASGITransport(app=w1_dispatch),
        "http://127.0.0.1:8102": httpx.ASGITransport(app=w2_dispatch),
    }

    test_worker_client = WorkerClient(
        token=os.environ["CLEANSLOT_TOKEN"], transports=transports
    )
    controller_main.worker_client = test_worker_client

    yield {
        "db_path": db_path,
        "worker_1_state": w1_state,
        "worker_2_state": w2_state,
    }


@pytest.fixture
def auth_headers():
    """Valid Bearer token headers."""
    return {"Authorization": "Bearer test-cleanslot-token-123"}


@pytest.fixture
def bad_auth_headers():
    """Invalid Bearer token headers."""
    return {"Authorization": "Bearer wrong-token-456"}


@pytest.fixture
def client():
    """Controller TestClient instance."""
    return TestClient(controller_app)


@pytest.fixture
def worker_client_test():
    """Worker TestClient instance."""
    return TestClient(worker_app)
