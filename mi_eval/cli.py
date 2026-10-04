"""Command line for the sleep-mi-v0 eval. See trajectories/sleep-mi-v0/README.md for the walkthrough.

    python -m mi_eval respond  --responder claude:claude-opus-5-5
    python -m mi_eval grade    --run runs/sleep-mi-v0/<run> --grader ollama:gemma4:12b
    python -m mi_eval run      --responder claude:claude-opus-5-5 --grader ollama:gemma4:12b
    python -m mi_eval selftest --grader ollama:gemma4:12b
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import grade as g
from .models import CallError, chat, family, parse_target

RUNS = g.HERE.parent / "runs" / "sleep-mi-v0"
MARK = {"PASS": "✅ pass", "FAIL": "❌ fail", "N/A": "— n/a", "ERROR": "⚠️ error"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(target: str) -> str:
    return target.replace(":", "_").replace("/", "_")


def _check_families(responder: str, grader: str, allow: bool) -> None:
    if family(responder) == family(grader) and not allow:
        sys.exit(f"The grader ({grader}) is from the same family as the model under test ({responder}). "
                 "README rule 5 says it must not be. Pass --allow-same-family only for a pipeline smoke test.")


# -- step 1: get the replies ----------------------------------------------------------------------

def respond(responder: str, effort: str | None, out_dir: Path | None) -> Path:
    parse_target(responder)
    script = g.load(g.SCRIPT)
    run_dir = out_dir or RUNS / f"{_now()}-{_slug(responder)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    messages, turns = [], []
    for moment, text in g.user_messages(script):
        messages.append({"role": "user", "content": text})
        print(f"{moment['id']}: asking {responder} ...", flush=True)
        out = chat(responder, messages, effort=effort)
        messages.append({"role": "assistant", "content": out["text"]})
        turns.append({"moment": moment["id"], "user": text, "reply": out["text"], "model": out["model"],
                      "usage": out["usage"], "latency_s": out["latency_s"], "stop_reason": out.get("stop_reason")})
        print(f"   {len(out['text'].split())} words in {out['latency_s']}s", flush=True)
    transcript = {"run_id": run_dir.name, "created_utc": _now(), "responder": responder,
                  "responder_family": family(responder), "settings": out["settings"],
                  "memory": "full history: every earlier message in one thread", "system_prompt": None,
                  "versions": g.versions(), "turns": turns}
    (run_dir / "transcript.json").write_text(json.dumps(transcript, indent=2, ensure_ascii=False))
    print(f"\nTranscript: {run_dir / 'transcript.json'}")
    return run_dir


# -- step 2: grade --------------------------------------------------------------------------------

def grade_run(run_dir: Path, grader: str, moments: set[str] | None, allow_same: bool) -> Path:
    transcript = json.loads((run_dir / "transcript.json").read_text())
    _check_families(transcript["responder"], grader, allow_same)
    script, rubric = g.load(g.SCRIPT), g.load(g.RUBRIC)
    by_id = {m["id"]: m for m in script["moments"]}
    out_path = run_dir / f"grades.{_slug(grader)}.jsonl"
    done = {}
    if out_path.exists():  # resumable: keep finished rows, redo errors
        for line in out_path.read_text().splitlines():
            row = json.loads(line)
            if row["outcome"] != "ERROR":
                done[(row["moment"], row["item"])] = row
    rows, conversation = list(done.values()), []
    with out_path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        for turn in transcript["turns"]:
            conversation.append({"role": "user", "content": turn["user"]})
            if moments is None or turn["moment"] in moments:
                for item in g.items_for(rubric, turn["moment"]):
                    if (turn["moment"], item["id"]) in done:
                        continue
                    result = g.grade_one(grader, item, by_id[turn["moment"]], list(conversation), turn["reply"])
                    row = {"moment": turn["moment"], "item": item["id"], "points": item["points"],
                           "grader": grader, "versions": g.versions(), "graded_utc": _now(), **result}
                    rows.append(row)
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    f.flush()
                    print(f"{turn['moment']:4} {item['id']:7} {MARK[row['outcome']]:9} ({row['latency_s']}s)"
                          + (f"  {row['error']}" if row["error"] else ""), flush=True)
            conversation.append({"role": "assistant", "content": turn["reply"]})
    report_path = run_dir / f"report.{_slug(grader)}.md"
    report_path.write_text(report(transcript, rows, grader, script, rubric))
    print(f"\nGrades: {out_path}\nReport: {report_path}")
    return report_path


def _score(rows: list[dict]) -> tuple[int, int]:
    """Points earned over the positive points of the items that applied (N/A and errors left out)."""
    live = [r for r in rows if r["outcome"] in ("PASS", "FAIL")]
    earned = sum(r["points"] for r in live if r["verdict"] == "met")
    possible = sum(r["points"] for r in live if r["points"] > 0)
    return earned, possible


def _cell(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def report(transcript: dict, rows: list[dict], grader: str, script: dict, rubric: dict) -> str:
    items = {i["id"]: i for i in rubric["always_on"]}
    for moment_items in rubric["moments"].values():
        items.update({i["id"]: i for i in moment_items})
    order = {m["id"]: n for n, m in enumerate(script["moments"])}
    rows = sorted(rows, key=lambda r: (order[r["moment"]], list(items).index(r["item"])))
    by_moment: dict[str, list[dict]] = {}
    for r in rows:
        by_moment.setdefault(r["moment"], []).append(r)

    count = lambda rs, o: sum(r["outcome"] == o for r in rs)  # noqa: E731
    out = [f"# MI grading report · {transcript['run_id']}", "",
           "| | |", "|---|---|",
           f"| Model under test | `{transcript['responder']}` (served as `{transcript['turns'][0]['model']}`) |",
           f"| Grader | `{grader}` |",
           f"| Settings | {_cell(json.dumps(transcript['settings']))}; no system prompt; full history |",
           *[f"| {k.replace('_', ' ').capitalize()} version | {v} |" for k, v in transcript["versions"].items()],
           f"| Replies written | {transcript['created_utc']} |", "",
           "**Read this first.** Grades are from one automated grader and have not been checked against a human "
           "label. Before trusting them, read the replies yourself and mark where you disagree (the self-test "
           "result for this grader says how far to trust it). An `error` is the grader failing, never the model.", "",
           "## Summary", "", "| Moment | What it tests | Pass | Fail | N/A | Error | Points |", "|---|---|---|---|---|---|---|"]
    all_live = []
    for m in script["moments"]:
        rs = by_moment.get(m["id"], [])
        if not rs:
            continue
        earned, possible = _score(rs)
        all_live += rs
        out.append(f"| {m['id']} | {m['title']} | {count(rs, 'PASS')} | {count(rs, 'FAIL')} | {count(rs, 'N/A')} | "
                   f"{count(rs, 'ERROR')} | {earned} / {possible} |")
    earned, possible = _score(all_live)
    out += [f"| **All** | | **{count(all_live, 'PASS')}** | **{count(all_live, 'FAIL')}** | {count(all_live, 'N/A')} | "
            f"{count(all_live, 'ERROR')} | **{earned} / {possible}** |", ""]

    ao = [r for r in all_live if r["item"].startswith("AO")]
    if ao:
        out += ["### Always-on checks across all eight exchanges", "", "| Check | Pass | Fail | N/A | Error |",
                "|---|---|---|---|---|"]
        for item_id in [i["id"] for i in rubric["always_on"]]:
            rs = [r for r in ao if r["item"] == item_id]
            out.append(f"| {item_id} {_cell(items[item_id]['criterion'])[:70]}… | {count(rs, 'PASS')} | "
                       f"{count(rs, 'FAIL')} | {count(rs, 'N/A')} | {count(rs, 'ERROR')} |")
        out.append("")

    fails = [r for r in all_live if r["outcome"] == "FAIL"]
    out += ["## Where it did not follow MI", ""]
    if not fails:
        out.append("No failed items.")
    for r in fails:
        kind = "did something MI says not to" if r["points"] < 0 else "missed something MI calls for"
        evidence = f" Evidence: \"{_cell(r['quote'])}\"" if r["verdict"] == "met" and r["quote"] else ""
        out.append(f"- **{r['moment']} · {r['item']}** ({kind}): {_cell(items[r['item']]['criterion'])}"
                   f"{evidence} *Grader:* {_cell(r['reasoning'])}")
    out.append("")

    out.append("## Exchange by exchange")
    turns = {t["moment"]: t for t in transcript["turns"]}
    for m in script["moments"]:
        rs = by_moment.get(m["id"])
        if not rs:
            continue
        turn = turns[m["id"]]
        out += ["", f"### {m['id']} · {m['title']}", "", f"*Tests:* {m['tests']}", "",
                f"**Bob:** {turn['user']}", "", "**Reply:**", "",
                *[f"> {line}" if line.strip() else ">" for line in turn["reply"].strip().splitlines()], "",
                "| Item | Pts | Checks | Result | Evidence / grader's reasoning |", "|---|---|---|---|---|"]
        for r in rs:
            why = r["error"] if r["outcome"] == "ERROR" else (
                (f"\"{_cell(r['quote'])}\" — " if r["verdict"] == "met" and r["quote"] else "") + _cell(r["reasoning"]))
            out.append(f"| {r['item']} | {r['points']:+d} | {_cell(items[r['item']]['criterion'])} | "
                       f"{MARK[r['outcome']]} | {_cell(why)} |")
    return "\n".join(out) + "\n"


# -- rule 4: the grader must get the known-pass and known-fail examples right -------------------------

def selftest(grader: str, moments: set[str] | None) -> int:
    parse_target(grader)
    script, rubric, examples = g.load(g.SCRIPT), g.load(g.RUBRIC), g.load(g.EXAMPLES)
    items = {i["id"]: i for i in rubric["always_on"]}
    for moment_items in rubric["moments"].values():
        items.update({i["id"]: i for i in moment_items})
    msgs = g.user_messages(script)
    run_dir = RUNS / "selftest" / f"{_now()}-{_slug(grader)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with (run_dir / "results.jsonl").open("w") as f:
        for ex in examples["examples"]:
            if moments is not None and ex["moment"] not in moments:
                continue
            # Bob's lines up to this moment; earlier assistant turns are marked placeholders.
            conversation = []
            for moment, text in msgs:
                if conversation:
                    conversation.append({"role": "assistant", "content": "[earlier assistant reply not shown]"})
                conversation.append({"role": "user", "content": text})
                if moment["id"] == ex["moment"]:
                    break
            moment = next(m for m in script["moments"] if m["id"] == ex["moment"])
            for item_id, expected in ex["expect"].items():
                result = g.grade_one(grader, items[item_id], moment, conversation, ex["reply"])
                right = None if result["verdict"] is None else result["verdict"] == expected
                row = {"example": ex["id"], "item": item_id, "expected": expected, "got": result["verdict"],
                       "right": right, "grader": grader, "versions": g.versions(), **result}
                rows.append(row)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                tag = "error" if right is None else ("right" if right else "WRONG")
                print(f"{ex['id']:8} {item_id:7} expected {expected:14} got {str(result['verdict']):14} {tag}"
                      f" ({result['latency_s']}s)", flush=True)

    wrong = [r for r in rows if r["right"] is False]
    errors = [r for r in rows if r["right"] is None]
    unusable = sorted({r["item"] for r in wrong + errors})
    lines = [f"# Grader self-test · {grader}", "", f"{run_dir.name} · " +
             " · ".join(f"{k} {v}" for k, v in g.versions().items()), "",
             f"- Verdicts checked: {len(rows)}",
             f"- Right: {sum(r['right'] is True for r in rows)}",
             f"- Wrong: {len(wrong)} (should-fail graded as met/pass-direction and vice versa are listed below)",
             f"- Grader errors (unparseable, bad quote): {len(errors)}", "",
             f"**Items not usable with this grader yet (rule 4):** {', '.join(unusable) or 'none'}", ""]
    if wrong or errors:
        lines += ["| Example | Item | Expected | Got | Grader's reasoning |", "|---|---|---|---|---|"]
        for r in wrong + errors:
            lines.append(f"| {r['example']} | {r['item']} | {r['expected']} | {r['got'] or 'error'} | "
                         f"{_cell(r['reasoning'] or r['error'])} |")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"\n{sum(r['right'] is True for r in rows)}/{len(rows)} right. Summary: {run_dir / 'summary.md'}")
    return 1 if unusable else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m mi_eval", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("respond", "run"):
        s = sub.add_parser(name)
        s.add_argument("--responder", required=True, help="model under test, e.g. claude:claude-opus-5-5")
        s.add_argument("--effort", default=None, help="Claude effort (low/medium/high/xhigh/max); default: API default")
        s.add_argument("--out", type=Path, default=None, help="run folder (default runs/sleep-mi-v0/<time>-<model>)")
    for name in ("grade", "run", "selftest"):
        s = sub.choices.get(name) or sub.add_parser(name)
        s.add_argument("--grader", required=True, help="e.g. ollama:gemma4:12b or deepseek:deepseek-flash")
        s.add_argument("--moments", default=None, help="only these moments, e.g. KM1,KM2")
        if name != "selftest":
            s.add_argument("--allow-same-family", action="store_true", help="smoke tests only (breaks rule 5)")
    sub.choices["grade"].add_argument("--run", type=Path, required=True, help="a run folder with transcript.json")
    a = p.parse_args(argv)
    moments = set(a.moments.split(",")) if getattr(a, "moments", None) else None
    try:
        if a.cmd == "respond":
            respond(a.responder, a.effort, a.out)
        elif a.cmd == "grade":
            grade_run(a.run, a.grader, moments, a.allow_same_family)
        elif a.cmd == "run":
            _check_families(a.responder, a.grader, a.allow_same_family)
            grade_run(respond(a.responder, a.effort, a.out), a.grader, moments, a.allow_same_family)
        else:
            return selftest(a.grader, moments)
    except (CallError, ValueError) as e:
        sys.exit(f"error: {e}")
    return 0
