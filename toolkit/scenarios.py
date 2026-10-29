"""Run scenarios, summarize records, compare them, format diffs, and JSON helpers.

Record format (recordings, fixtures and real-run results alike):
    {"scenario", "description", "source", "model"?, "generated_at",
     "steps": [{"call", "args" (unresolved), "kind": "success"|"error", "http_status", "body"}]}
"""

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from toolkit import ui
from toolkit.gateway import GatewayError

_REF = re.compile(r"^\$step(\d+)\.id$")
SUMMARY_FIELDS_ORDER = ("ok", "object", "status", "amount", "currency",
                        "http_status", "error_type", "error_code", "decline_code")


# ---- JSON helpers ---------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def load_records(directory) -> dict:
    """{scenario name: record} for every *.json in directory (unreadable files skipped)."""
    out = {}
    directory = Path(directory)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            rec = load_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and isinstance(rec.get("steps"), list):
            out[rec.get("scenario") or path.stem] = rec
    return out


def strip_secrets(obj):
    """Deep copy with every client_secret removed (never send those to Claude)."""
    if isinstance(obj, dict):
        return {k: strip_secrets(v) for k, v in obj.items() if k != "client_secret"}
    if isinstance(obj, list):
        return [strip_secrets(v) for v in obj]
    return obj


def compact(obj) -> str:
    return json.dumps(strip_secrets(obj), separators=(",", ":"), ensure_ascii=False)


# ---- running scenarios ----------------------------------------------------------

def resolve_args(args: dict, ids: list) -> dict:
    out = {}
    for key, value in (args or {}).items():
        m = _REF.match(value) if isinstance(value, str) else None
        if m:
            n = int(m.group(1))
            value = ids[n] if n < len(ids) else None
        out[key] = value
    return out


def run_scenario(gateway, scenario: dict, provider) -> dict:
    """Execute every step against the gateway and return a record (args stay unresolved)."""
    ids, steps = [], []
    for step in scenario["steps"]:
        args = resolve_args(step.get("args", {}), ids)
        try:
            body = getattr(gateway, step["call"])(**args)
            kind, status = "success", 200
        except GatewayError as e:
            kind, status, body = "error", e.http_status, e.body
        except Exception as e:  # a crashing fake should read as a diff, not a traceback
            kind, status = "error", 0
            body = {"error": {"type": "python_exception", "code": type(e).__name__,
                              "message": f"{type(e).__name__}: {e}"}}
        if not isinstance(body, dict):
            body = {"error": {"type": "python_exception", "code": "NonDictResult",
                              "message": f"{step['call']} returned {type(body).__name__}, not a dict"}}
            kind, status = "error", 0
        ids.append(provider.resource_id(kind, body))
        steps.append({"call": step["call"], "args": copy.deepcopy(step.get("args", {})),
                      "kind": kind, "http_status": status, "body": body})
    return {"scenario": scenario["name"], "description": scenario.get("description", ""),
            "steps": steps}


# ---- summaries and comparison ---------------------------------------------------

def summarize_record(record: dict, provider) -> list:
    return [provider.summarize(s.get("kind"), s.get("http_status"), s.get("body") or {})
            for s in record.get("steps", [])]


def describe(summary) -> str:
    if summary is None:
        return "(no step)"
    head = "success" if summary.get("ok") else "error  "
    parts = [f"{k}={summary[k]}" for k in SUMMARY_FIELDS_ORDER if k != "ok" and k in summary]
    return f"{head}  " + " ".join(parts)


def compare(expected: dict, actual: dict, provider) -> list:
    """[{index, call, expected, actual, fields}] for every step whose summary differs."""
    exp, act = summarize_record(expected, provider), summarize_record(actual, provider)
    exp_steps, act_steps = expected.get("steps", []), actual.get("steps", [])
    diffs = []
    for i in range(max(len(exp), len(act))):
        e = exp[i] if i < len(exp) else None
        a = act[i] if i < len(act) else None
        if e != a:
            src = exp_steps[i] if i < len(exp_steps) else act_steps[i]
            keys = sorted(set(e or {}) | set(a or {}))
            fields = [k for k in keys if (e or {}).get(k) != (a or {}).get(k)]
            diffs.append({"index": i, "call": src.get("call"), "expected": e, "actual": a,
                          "fields": fields})
    return diffs


def setup_problem(records: dict):
    """The first step of every record failed with one identical error message: that is a broken setup
    (a rejected parameter, an account setting), not service behavior. Returns the message, or None.
    Only first steps count, because later steps fail as a knock-on effect (no id to act on)."""
    steps = [(r or {}).get("steps", [])[0] for r in records.values() if (r or {}).get("steps")]
    if len(steps) < 2 or any(s.get("kind") != "error" for s in steps):
        return None
    messages = {(((s.get("body") or {}).get("error") or {}).get("message") or "") for s in steps}
    if len(messages) != 1:
        return None
    return (messages.pop() or "no error message").splitlines()[0][:300]


def format_diff(name: str, diffs: list, expected_label: str, actual_label: str) -> str:
    width = max(len(expected_label), len(actual_label)) + 2
    n = len(diffs)
    lines = [f"{name}: {n} step{'s' if n != 1 else ''} differ{'s' if n == 1 else ''}"]
    for d in diffs:
        lines.append(f"  step {d['index']} {ui.SYM['dot']} {d['call']}")
        lines.append(f"    {expected_label.ljust(width)} {describe(d['expected'])}")
        lines.append(f"    {actual_label.ljust(width)} {describe(d['actual'])}")
        if d["expected"] and d["actual"] and d["expected"].get("ok") == d["actual"].get("ok"):
            changed = ", ".join(f"{k}: {d['expected'].get(k)!r} {ui.SYM['arrow']} {d['actual'].get(k)!r}"
                                for k in d["fields"])
            lines.append(f"    differs in: {changed}")
    return "\n".join(lines)


def outcome_line(record: dict, provider) -> str:
    """One-line outcome summary, e.g. 'create_payment → success payment_intent/succeeded'."""
    parts = []
    for step, s in zip(record.get("steps", []), summarize_record(record, provider)):
        if s.get("ok"):
            parts.append(f"{step.get('call')} {ui.SYM['arrow']} ok {s.get('object')}/{s.get('status')}")
        else:
            bits = [str(s.get(k)) for k in ("error_type", "error_code", "decline_code") if s.get(k)]
            parts.append(f"{step.get('call')} {ui.SYM['arrow']} {s.get('http_status')} {'/'.join(bits)}")
    return "; ".join(parts)


# ---- complexity stats (record.py) -----------------------------------------------

def count_fields(obj) -> int:
    if isinstance(obj, dict):
        return sum(1 + count_fields(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(count_fields(v) for v in obj)
    return 0


def depth(obj) -> int:
    if isinstance(obj, dict):
        return 1 + max((depth(v) for v in obj.values()), default=0)
    if isinstance(obj, list):
        return max((depth(v) for v in obj), default=0)
    return 0
