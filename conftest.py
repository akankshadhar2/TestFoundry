"""--backend=fake|real|replay, gateway fixtures, and saving real-run results for repair/replay."""

import time

import pytest

from toolkit import config as cfg, fakes, scenarios, ui

cfg.load_env()
PROVIDER = cfg.load_provider()

REAL_RESULTS = {}      # scenario name -> record, filled by test_contract on --backend=real
_state = {"started": None, "saved_to": None}


def pytest_addoption(parser):
    parser.addoption("--backend", choices=["fake", "real", "replay"], default="fake",
                     help="fake: generated in-memory fake (default); real: the real service in test mode; "
                          "replay: real responses saved during rehearsal")


def _backend(config_):
    return config_.getoption("--backend")


def pytest_report_header(config):
    return f"backend: {_backend(config)}   provider: {PROVIDER.DISPLAY_NAME} ({PROVIDER.NAME})"


def pytest_sessionstart(session):
    _state["started"] = time.perf_counter()
    backend = _backend(session.config)
    if backend == "real":
        problem = PROVIDER.key_problem()
        if problem:
            pytest.exit(f"Can't run against real {PROVIDER.DISPLAY_NAME}: {problem.rstrip('.')}. "
                        "Tip: use --backend=replay to use responses saved during rehearsal.", returncode=4)
    elif backend == "replay" and not cfg.real_run_path(PROVIDER).is_file():
        pytest.exit("No real-run recording. Run --backend=real once during rehearsal.", returncode=4)


# ---- fixtures -------------------------------------------------------------------

@pytest.fixture(scope="session")
def backend(request):
    return _backend(request.config)


@pytest.fixture(scope="session")
def provider():
    return PROVIDER


@pytest.fixture(scope="session")
def fake_class():
    path = cfg.fake_path(PROVIDER)
    if not path.is_file():
        pytest.fail("No generated fake. Run: python generate_fake.py", pytrace=False)
    try:
        module = fakes.load_module(path, name=f"generated_fake_{PROVIDER.NAME}")
    except Exception as e:
        pytest.fail(f"The generated fake failed to import: {type(e).__name__}: {e}. "
                    "Run: python generate_fake.py  (or python reset.py --from-backup)", pytrace=False)
    cls = getattr(module, PROVIDER.FAKE_CLASS, None)
    if cls is None:
        pytest.fail(f"{cfg.rel(path)} has no class {PROVIDER.FAKE_CLASS}", pytrace=False)
    return cls


@pytest.fixture(scope="session")
def real_gateway():
    try:
        return PROVIDER.make_real_gateway()
    except Exception as e:
        pytest.exit(f"Can't create the real {PROVIDER.DISPLAY_NAME} client: {e}", returncode=4)


@pytest.fixture
def gateway(request, backend):
    """A fresh fake per test, or the shared real client. App tests skip on replay."""
    if backend == "replay":
        pytest.skip("app tests need a live backend (replay only covers contract tests)")
    if backend == "real":
        return request.getfixturevalue("real_gateway")
    return request.getfixturevalue("fake_class")()


@pytest.fixture(scope="session")
def real_run():
    data = scenarios.load_json(cfg.real_run_path(PROVIDER))
    return data.get("records", {})


@pytest.fixture(scope="session")
def real_results():
    return REAL_RESULTS


# ---- saving real results and summaries ------------------------------------------

def pytest_sessionfinish(session, exitstatus):
    if _backend(session.config) != "real" or not REAL_RESULTS:
        return
    path = cfg.real_run_path(PROVIDER)
    data = {}
    if path.is_file():
        try:
            data = scenarios.load_json(path)
        except (OSError, ValueError):
            data = {}
    broken = scenarios.setup_problem(REAL_RESULTS)
    if broken:
        _state["setup_problem"] = broken
        return
    records = data.get("records", {}) if isinstance(data.get("records"), dict) else {}
    records.update(REAL_RESULTS)
    scenarios.save_json(path, {"provider": PROVIDER.NAME, "recorded_at": scenarios.now_iso(),
                               "records": records})
    _state["saved_to"] = path


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    backend = _backend(config)
    stats = terminalreporter.stats
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", [])) + len(stats.get("error", []))
    elapsed = time.perf_counter() - (_state["started"] or time.perf_counter())
    tr = terminalreporter
    if backend == "fake" and passed and not failed:
        tr.write_line(f"{ui.SYM['bolt']} {passed} tests in {elapsed:.2f} s, no network", green=True, bold=True)
    if _state["saved_to"]:
        tr.write_line(f"Real responses saved to {cfg.rel(_state['saved_to'])}")
    if _state.get("setup_problem"):
        tr.write_line(f"Every real {PROVIDER.DISPLAY_NAME} call failed with the same error. That is a setup "
                      "problem, not an AI mistake, so nothing was saved and there is nothing to repair.",
                      red=True, bold=True)
        tr.write_line(f"{PROVIDER.DISPLAY_NAME} said: {_state['setup_problem']}", red=True)
        return
    if backend in ("real", "replay") and failed:
        tr.write_line(f"Real {PROVIDER.DISPLAY_NAME} disagreed with the AI's expectations. "
                      "Next: python repair_fake.py", yellow=True, bold=True)
