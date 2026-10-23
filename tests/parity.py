"""Today's vaping-v0 behavior, worked out from tracked files, so a new loader or engine can be held to it.

    python -m tests.parity --write     # rebuild tests/fixtures/vaping-v0/*.json from the current code

Four things are pinned (infra audit 4.4): the messages each model under test received at the 13 Oct 8
checkpoints, the grader prompts, the scores re-added from the saved Oct 8 verdicts, and the compare and review
outputs from the committed labels. tests/test_parity.py checks the current code against the fixtures. Rebuild them
only for a change the team decided on (a reworded line, new points, a scoring rule), and commit the new fixtures
on their own, saying why, so a side effect never slips in beside a decision.
"""

import contextlib
import csv
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

from bcb import compare, examples, grader, item_tests, labels as lb, pilot, review, review_import
from bcb.score import clipped_mean, reply_score
from bcb.spec import Spec, load_conversation

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/vaping-v0"
# the 13 checkpoints graded for Oct 8: the main line, then the three branches carried from the messy pilot
CHECKPOINTS = ["KM1", "KM2", "KM3", "KM4", "KM5", "KM6", "KM7", "KM7-T", "KM7-P", "KM8",
               "MS-DECIDE", "MS-WHOLEPLAN", "MS-LAPSE"]
LABELS = ROOT / "runs/oct4-labels/labels.jsonl"
COMMITTED = ROOT / "runs/oct8-graded/compare"
REVIEW = ROOT / "runs/oct8-graded/review"
# the tracked files the fixtures are worked out from, under the name a failure message gives them
INPUTS = {
    "spec.yaml": [lb.SPEC],
    "conversation.v1.0.yaml": [lb.CONVERSATION],
    "messy-pilot.yaml": [lb.MESSY],
    "item-examples.yaml": [item_tests.DEFAULT_EXAMPLES],
    "the saved Oct 8 replies and verdicts (runs/oct8-graded/*/units.jsonl)":
        [d / name for d in review.GRADED for name in ("units.jsonl", "source.json")],
    "the Oct 4 labels (runs/oct4-labels/labels.jsonl)": [LABELS],
    "the review key and Sheets (runs/oct8-graded/review)":
        [REVIEW / "review-key.json"] + [REVIEW / folder / f"Review - {p}.xlsx" for p in review.PLAN
                                        for folder in ("locked", f"Review - {p}")],
    "the committed review table (runs/oct8-graded/compare/disagreements.reviewed.csv)":
        [COMMITTED / "disagreements.reviewed.csv"],
}
COMPARE_FILES = ["per_item.csv", "disagreements.primary.csv", "people_disagreements.csv", "summary.md", "scores.md"]
REVIEW_FILES = ["disagreements.reviewed.csv", "scores.reviewed.md"]
# one reply graded on every check at every checkpoint, so prompts differ only in what the spec puts around it
FIXED_REPLY = ("It sounds like part of you wants to stop and part of you isn't sure yet. "
               "What would make it feel worth trying?")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def short(text):
    return sha(text)[:16]


def provenance():
    """A sha256 per input in INPUTS (over its files, in order), so a failure can name which input changed since."""
    out = {}
    for name, paths in INPUTS.items():
        h = hashlib.sha256()
        for p in paths:
            h.update(p.read_bytes())
        out[name] = h.hexdigest()
    return out


# ---------- model inputs ----------

def model_inputs(spec):
    """Per checkpoint: a sha256 over its message list, and one line per message (turn, role, hash of the text)."""
    out = {}
    for c in CHECKPOINTS:
        messages = spec.messages(c)
        out[c] = {"sha256": sha(json.dumps(messages, sort_keys=True)),
                  "messages": [f"{t.id} {m['role']} {short(m['content'])}" for t, m in zip(spec.turns(c), messages)]}
    return out


def all_inputs_sha(spec):
    """The figure the audit quotes: checkpoints in sorted order, each message list as sorted-key JSON, concatenated."""
    return sha("".join(json.dumps(spec.messages(c), sort_keys=True) for c in sorted(CHECKPOINTS)))


# ---------- grader prompts ----------

def checkpoint_prompts(spec):
    """Per checkpoint, every check it grades, in order, with the hash of its prompt on FIXED_REPLY."""
    return {c: [f"{i} {short(grader.render_prompt(spec, c, spec.items[i], FIXED_REPLY))}" for i in spec.items_at(c)]
            for c in CHECKPOINTS}


def oct8_prompts(spec, units):
    """Per checkpoint, the saved verdicts and one sha256 over the prompt each would be graded with again.

    The reply text is what the grader read on Oct 8 (normalized, as the unit records), as bcb.pilot.regrade_errors
    picks it.
    """
    lines = {c: [] for c in CHECKPOINTS}
    for u in units:
        text = lb.normalize(u["reply"]) if u.get("grader_read") == "normalized" else u["reply"]
        for v in u["verdicts"]:
            prompt = grader.render_prompt(spec, u["checkpoint"], spec.items[v["item"]], text)
            lines[u["checkpoint"]].append(f"{u['model']} {u['sample']} {v['item']} {sha(prompt)}")
    return {c: {"verdicts": len(xs), "sha256": sha("\n".join(sorted(xs)))} for c, xs in lines.items()}


def example_prompts(conversation=None):
    """Every active item-test example's prompt, as bcb.item_tests renders it (with or without the frozen conversation)."""
    spec = Spec(item_tests.DEFAULT_SPEC, conversation=load_conversation(conversation) if conversation else None)
    _, found = examples.load(item_tests.DEFAULT_EXAMPLES)
    return [f"{e.id} {short(grader.render_prompt(spec, e.checkpoint, spec.items[e.item], e.reply))}"
            for e in found if e.status == "active"]


# ---------- scores ----------

def graded_units():
    """The replies graded for Oct 8, read straight from the tracked units.jsonl files; a later line wins, as on resume."""
    units = {}
    for d in review.GRADED:
        for line in (d / "units.jsonl").read_text().splitlines():
            if line.strip():
                u = json.loads(line)
                units[(u["checkpoint"], u["model"], u["sample"])] = u
    return [u for u in units.values() if not u["error"]]


def reply_key(u):
    return f"{u['checkpoint']} {u['model']} #{u['sample']}"


def rescored(spec, units):
    """Each unit with its score added up again from its saved verdicts, the way the runner scores a reply."""
    return [pilot.score_unit(spec, dict(u)) for u in units]


def fixed_na(spec, checkpoint):
    """The always-on checks the spec fixes as N/A at a checkpoint (its always_on map); AO4 at KM1, KM2, KM5, KM6."""
    return sorted(k for k, v in (spec.moments[checkpoint].get("always_on") or {}).items() if v == "not_applicable")


def model_scores(spec, units):
    """Per model, the figures in the model rows of scores.md and scores.reviewed.md, at full precision: the score,
    main line and carried apart, core and scenario, the interval, and the scores after the disagreement review."""
    scored = rescored(spec, units)
    merged = list(csv.DictReader((COMMITTED / "disagreements.reviewed.csv").read_text().splitlines(keepends=True)))
    reviewed = review_import.rescore(dict(enumerate(scored)), merged, spec)
    # the same checkpoint order compare.scores hands to the bootstrap: main line, then carried, each sorted
    order = sorted({u["checkpoint"] for u in scored}, key=lambda c: (c not in lb.MAIN, c))
    parts = {"all": lambda u: True, "main": lambda u: u["checkpoint"] in lb.MAIN,
             "carried": lambda u: u["checkpoint"] not in lb.MAIN}
    out = {}
    for m in sorted({u["model"] for u in scored}):
        mine = [u for u in scored if u["model"] == m]
        ci = pilot.clustered_interval({c: [u["score"] for u in mine if u["checkpoint"] == c] for c in order})
        out[m] = {"replies": len(mine),
                  **{k: clipped_mean([u["score"] for u in mine if keep(u)]) for k, keep in parts.items()},
                  "core": clipped_mean([pilot.subscore(spec, u, core=True) for u in mine]),
                  "scenario": clipped_mean([pilot.subscore(spec, u, core=False) for u in mine]),
                  "ci95": list(ci),
                  "reviewed": {k: clipped_mean([u["score"] for u in reviewed if u["model"] == m and keep(u)])
                               for k, keep in parts.items()},
                  "verdicts_changed_by_review": sum(u["changed"] for u in reviewed if u["model"] == m)}
    return out


def fixed_na_rows(spec, units):
    """The rows the spec fixes as N/A, how many the grader did not call N/A (and so count today), and the scores
    with those rows left out, which is what honoring the spec would give. The team has not decided which is right."""
    rows = [(u, v) for u in units for v in u["verdicts"] if v["item"] in fixed_na(spec, u["checkpoint"])]
    left_out = {}
    for m in sorted({u["model"] for u in units}):
        left_out[m] = clipped_mean([reply_score([(spec.items[v["item"]], v["verdict"]) for v in u["verdicts"]
                                                 if v["item"] not in fixed_na(spec, u["checkpoint"])])
                                    for u in units if u["model"] == m])
    return {"rows": len(rows), "counted": sorted(f"{reply_key(u)} {v['item']} {v['verdict']}" for u, v in rows
                                                 if v["verdict"] in ("met", "not_met")),
            "if_left_out": left_out}


def gate_verdicts(spec, units):
    """Per checkpoint, how the grader called each gate; no gate tripped on Oct 8, so a gate cap would not move a score."""
    out = {}
    for u in sorted(units, key=lambda u: CHECKPOINTS.index(u["checkpoint"])):
        for v in u["verdicts"]:
            if spec.items[v["item"]].type == "gate":
                counts = out.setdefault(f"{u['checkpoint']} {v['item']}", {})
                counts[str(v["verdict"])] = counts.get(str(v["verdict"]), 0) + 1
    return out


# ---------- compare and review outputs ----------

def _quietly(tool, args, refused):
    """Run a tool's main(); if it refuses, raise with what it printed, so the failure says why."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
        code = tool(args)
    if code:
        raise RuntimeError(f"{refused}: {' / '.join(err.getvalue().strip().splitlines()[:5])}")


def _hashes(out, names):
    return {name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in names}


def rerun_compare(out):
    """Run bcb.compare on the three graded folders and the Oct 4 labels into `out`; {file name: sha256}."""
    _quietly(compare.main, [*map(str, review.GRADED), "--labels", str(LABELS), "--out", str(out)],
             "bcb.compare refused the Oct 8 graded folders")
    return _hashes(out, COMPARE_FILES)


def rerun_review_import(out):
    """Run bcb.review_import on the locked review Sheets into `out`, reading the disagreement table rerun_compare
    wrote there, so the chain is checked end to end; {file name: sha256}."""
    _quietly(review_import.main, [str(REVIEW / "locked"), "--build", str(REVIEW), "--table",
                                  str(out / "disagreements.primary.csv"), "--out", str(out / "disagreements.reviewed.csv")],
             "bcb.review_import refused the locked review Sheets")
    return _hashes(out, REVIEW_FILES)


def rerun_outputs(out):
    """Both steps into `out`, as the fixture holds them."""
    return {**rerun_compare(out), **rerun_review_import(out)}


# ---------- writing the fixtures ----------

def build(tmp):
    spec, _ = lb.load_spec()
    units = graded_units()
    about = {"inputs": provenance(), "rebuild": "python -m tests.parity --write, only for a decided change"}
    return {
        "model-inputs.json": {
            "about": dict(about, what="The messages each model under test received at the 13 Oct 8 checkpoints, as "
                          "bcb.labels.load_spec() renders them (spec, frozen conversation v1.0, messy-pilot branches). "
                          "One line per message: turn, role, first 16 hex of the sha256 of its text."),
            "all_sha256": all_inputs_sha(spec), "checkpoints": model_inputs(spec)},
        "grader-prompts.json": {
            "about": dict(about, what="Grader prompts: every check at every Oct 8 checkpoint on one fixed reply, in "
                          "the order items_at gives them; every saved Oct 8 verdict's prompt on the text the grader "
                          "read; every active item-test example as bcb.item_tests renders it, without and with the "
                          "frozen conversation."),
            "template": {"version": grader.PROMPT_VERSION, "sha": grader.prompt_hash()},
            "fixed_reply": FIXED_REPLY, "checkpoints": checkpoint_prompts(spec), "oct8": oct8_prompts(spec, units),
            "item_tests": {"without_conversation": example_prompts(),
                           "with_conversation": example_prompts(lb.CONVERSATION)}},
        "scores.json": {
            "about": dict(about, what="Scores re-added from the saved Oct 8 verdicts in runs/oct8-graded with the "
                          "current spec, at full precision: every reply, the model rows of scores.md and "
                          "scores.reviewed.md, the rows the spec fixes as N/A, and the gate verdicts."),
            "replies": {reply_key(u): u["score"] for u in rescored(spec, units)},
            "models": model_scores(spec, units),
            "fixed_na": dict(fixed_na_rows(spec, units),
                             decision="undecided: spec grading.static_na grades these rows so N/A agreement is "
                                      "measured, and docs/decisions.md does not say whether they count in the score. "
                                      "Today they count; if_left_out is what honoring the spec's fixed N/A would give"),
            "gates": gate_verdicts(spec, units)},
        "compare-outputs.json": {
            "about": dict(about, what="sha256 of each file bcb.compare and bcb.review_import write from the committed "
                          "labels, graded folders and locked review Sheets; the committed copies are in "
                          "runs/oct8-graded/compare."),
            "files": rerun_outputs(tmp)},
    }


def main(argv=None):
    if (argv if argv is not None else sys.argv[1:]) != ["--write"]:
        print(__doc__, file=sys.stderr)
        return 2
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in build(Path(tmp)).items():
            (FIXTURES / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
            print(f"wrote {FIXTURES / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
