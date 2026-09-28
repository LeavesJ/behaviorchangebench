"""End to end on the real spec with mock providers: draft, replies, packets, item tests, grade,
blind labels, tabulate. Checks the joins, resumability and the disagreement tables."""

from __future__ import annotations

import csv
import json
import re

from bcb.cli import main
from bcb.packets import LABEL_COLUMNS
from bcb.util import read_jsonl


def _run(*args):
    return main([*args])


def test_full_pipeline_with_labels(drafted):
    cfg = str(drafted / "run.yaml")
    run = drafted / "runs" / "smoke"
    assert _run("replies", "--config", cfg) == 0
    assert len(read_jsonl(run / "replies.jsonl")) == 9 * 2 * 3
    assert _run("replies", "--config", cfg) == 0  # resume: nothing left to request

    assert _run("packets", "--config", cfg) == 0
    key = json.loads((run / "sealed" / "packet-key.json").read_text())["replies"]
    packet = (run / "packets" / "KM1.md").read_text()
    assert not re.search(r"\bM[12]\b|mock-reply", packet)  # blind to model identity

    assert _run("examples-skeleton", "--config", cfg) == 0
    assert _run("itemtest", "--config", cfg, "--grader", "primary") == 0
    assert _run("grade", "--config", cfg, "--grader", "primary") == 2  # refuses: no item has passed its tests
    assert _run("grade", "--config", cfg, "--grader", "primary", "--allow-untested-items") == 0
    grades = read_jsonl(run / "grades.primary.jsonl")
    assert len(grades) == 79 * 2 * 3 and all(g["item_test_status"] == "untested" for g in grades)

    by_unit = {(g["checkpoint"], g["item_id"], g["model_slot"], g["sample"]): g for g in grades}
    with open(run / "packets" / "labels.template.csv", newline="") as f:
        template = [r for r in csv.DictReader(f) if r["checkpoint"] == "KM1"]
    flipped = disputed = None
    labels_dir = run / "labels"
    labels_dir.mkdir()
    for labeler in ("ann", "ben"):
        rows = []
        for r in template:
            if labeler == "ben" and r["double_coded"] != "yes":
                continue
            k = key[r["reply_id"]]
            g = by_unit[(k["checkpoint"], r["item_id"], k["model_slot"], k["sample"])]
            verdict, quote = g["verdict"], g["quote"] or ""
            if labeler == "ann" and flipped is None and verdict == "met" and r["double_coded"] == "no":
                flipped, verdict, quote = r, "not_met", ""
            elif labeler == "ben" and disputed is None and r is not flipped and verdict == "not_met":
                disputed, verdict = r, "not_applicable"
            rows.append({**r, "verdict": verdict, "quote": quote,
                         "reason": "does not apply" if verdict == "not_applicable" else "",
                         "labeler": labeler, "labeled_at": "2026-10-04T12:00:00Z"})
        with open(labels_dir / f"{labeler}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
            w.writeheader()
            w.writerows(rows)
    assert flipped is not None and disputed is not None

    assert _run("tabulate", "--config", cfg) == 0
    tables = run / "tables"
    with open(tables / "disagreements.primary.csv", newline="") as f:
        dis = list(csv.DictReader(f))
    assert [(d["reply_id"], d["item_key"]) for d in dis] == [(flipped["reply_id"], f"{flipped['item_id']}@0.1")]
    assert dis[0]["judge_verdict"] == "met" and dis[0]["manual_verdict"] == "not_met" and dis[0]["cause"] == ""
    with open(tables / "human_disagreements.csv", newline="") as f:
        hum = list(csv.DictReader(f))
    assert len(hum) == 1 and hum[0]["reply_id"] == disputed["reply_id"]
    with open(tables / "agreement.primary.csv", newline="") as f:
        agreement = list(csv.DictReader(f))
    total = [a for a in agreement if a["scope"] == "rubric"]
    assert sum(int(a["n_unresolved_human"]) for a in total) == 1
    manifest = json.loads((run / "run_manifest.json").read_text())
    assert manifest["contains_mock_output"] is True and manifest["labels"]["labelers"] == ["ann", "ben"]
    assert set(manifest["files"]) == {"replies.jsonl", "grades.primary.jsonl"}


def test_labels_are_validated(drafted):
    cfg = str(drafted / "run.yaml")
    run = drafted / "runs" / "smoke"
    for step in (("replies",), ("packets",), ("grade", "--grader", "primary", "--allow-untested-items")):
        _run(step[0], "--config", cfg, *step[1:])
    with open(run / "packets" / "labels.template.csv", newline="") as f:
        first = next(csv.DictReader(f))
    (run / "labels").mkdir()
    with open(run / "labels" / "zoe.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
        w.writeheader()
        w.writerow({**first, "verdict": "met", "quote": "words that are not in the reply",
                    "labeler": "zoe", "labeled_at": "2026-10-04"})
    assert _run("tabulate", "--config", cfg) == 2
