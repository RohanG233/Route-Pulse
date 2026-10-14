"""Run all Phase 1-6 tests. Clears __pycache__ first to avoid stale bytecode."""
import subprocess, sys, pathlib, shutil

repo = pathlib.Path(__file__).parent
for cache in repo.rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)

result = subprocess.run(
    [
        sys.executable, "-m", "pytest",
        "app/tests/test_dataset.py",
        "app/tests/test_eta.py",
        "app/tests/test_constraints.py",
        "app/tests/test_metrics.py",
        "app/tests/test_planner.py",
        "app/tests/test_api.py",
        "app/tests/test_edge_cases.py",
        "-v", "--tb=short", "-p", "no:cacheprovider",
    ],
    capture_output=True,
    text=True,
    cwd=str(repo),
)
output = result.stdout + result.stderr
pathlib.Path("test_results.txt").write_text(output, encoding="utf-8")
print(output)
sys.exit(result.returncode)
