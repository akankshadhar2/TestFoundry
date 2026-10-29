"""GENERIC contract tests: replay every scenario against the backend and compare to the fixtures.

Fixtures are the AI's expectations and the fake is built from them, so the fake passes.
The real run shows wherever those expectations were wrong.
"""

import pytest

from toolkit import config, scenarios

PROVIDER = config.load_provider()
NAMES = [s["name"] for s in PROVIDER.SCENARIOS]
BY_NAME = {s["name"]: s for s in PROVIDER.SCENARIOS}


@pytest.mark.parametrize("name", NAMES, ids=NAMES)
def test_contract(name, request, backend, provider):
    path = config.fixtures_dir(provider) / f"{name}.json"
    if not path.is_file():
        pytest.fail(f"No fixture for {name}. Run: python generate_fixtures.py", pytrace=False)
    fixture = scenarios.load_json(path)

    if backend == "replay":
        actual = request.getfixturevalue("real_run").get(name)
        if actual is None:
            pytest.skip(f"no real response recorded for {name}")
    else:
        actual = scenarios.run_scenario(request.getfixturevalue("gateway"), BY_NAME[name], provider)

    if backend == "real":
        problem = provider.transport_problem(actual)
        if problem:
            pytest.exit(f"{problem} Tip: use --backend=replay", returncode=4)
        actual.update({"source": "real-run", "generated_at": scenarios.now_iso()})
        request.getfixturevalue("real_results")[name] = actual

    diffs = scenarios.compare(fixture, actual, provider)
    if diffs:
        if backend == "fake":
            labels = ("fixture expects", "fake did")
        else:
            labels = ("AI fixture expected", f"real {provider.DISPLAY_NAME} did")
        pytest.fail(scenarios.format_diff(name, diffs, *labels), pytrace=False)
