# Rules for the AI agent (CleanSlot mock-first build)

PROJECT: CleanSlot, a small two-node VM controller (PS-023 prototype).
This version uses a MOCK worker. Never claim or imply that real VMs run.

SAFETY RULES
1. Never run: pkill, killall, taskkill, rm -rf, del /s, rmdir /s, format,
   git push --force, git reset --hard, or anything needing admin/sudo.
2. Never call os.kill, subprocess kill, or any OS-level kill on a fake PID.
   Fake PIDs are only keys in a Python dictionary.
3. Only create or edit files inside this project folder.
4. Install only: fastapi, uvicorn, pydantic, pytest, httpx. Ask before anything else.
5. Bind servers to 127.0.0.1 only.
6. Ask me before running any terminal command, and show the exact command.

DESIGN RULES
7. All requests need "Authorization: Bearer <token>" where the token comes from the
   CLEANSLOT_TOKEN environment variable. Compare with hmac.compare_digest.
8. Placement: safe_free = min(mem_available, budget - reserved) - safety_buffer.
   A VM fits if safe_free >= requested. Choose the eligible host with most safe_free.
9. Limits: RAM 512-2048 MiB, vCPU 1-2. Reject before contacting any worker.
10. Labels (exactly four): ACTIVE, STOPPED_CLEAN, FAILED_CLEANUP_NEEDED, UNKNOWN.
11. Never stop or clean an ACTIVE VM automatically. Only an explicit stop request may stop it.
12. Cleanup verifies identity (stored pid, start_time, cmdline) before removing a VM.
    If identity cannot be verified, do nothing destructive and mark UNKNOWN.
13. Release memory reservation only after the worker confirms cleanup.
14. Every rejection and cleanup must explain WHY (reasons in the response).
15. Never print or log the token.

CODE RULES
16. Python 3.11+, FastAPI, SQLite (sqlite3 module), type hints.
17. Put a 3-5 line comment at the top of every file saying what it does and why.
18. Write a pytest test for every new behaviour. Tests must pass before you say you are done.
19. Keep it small. Do not add features I did not ask for.
20. After each task, list: files changed, how to run it, how to run the tests.