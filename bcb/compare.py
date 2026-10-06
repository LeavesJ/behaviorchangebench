"""Step 4: the grader's verdicts beside the Oct 4 labels, item by item, with every disagreement.

    python -m bcb.compare runs/oct8-graded/<run> [...] --labels runs/oct4-labels/labels.jsonl --out runs/oct8-graded/compare

The grader is compared with the first coder's label (decisions.md 2026-10-02). On the double-coded replies the
second coder's label gives agreement between people, and the grader is set beside both. AO4 rows the spec fixes
(source "spec") are compared with the grader apart from people. A grader cannot_determine on the KM7 gate is its
own category, never agreement or disagreement (decisions.md 2026-10-05). Rows a labeler marked unsure are counted
in the totals and apart.

Writes per_item.csv, disagreements.primary.csv, people_disagreements.csv, summary.md, and scores.md (the points
score over every graded reply, main line and carried branches together and apart). Raw agreement and the
tables only: no kappa, since the counts per item are small (spec labels.reporting). A run folder graded on raw
text, an ungraded reply, or a label with no graded reply stops the comparison.
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import grader, labels as lb, pilot
from .score import clipped_mean

ROOT = Path(__file__).resolve().parent.parent
GATE_UNDETERMINED = "cannot_determine"
SHORT = {"met": "met", "not_met": "not met", "not_applicable": "N/A", GATE_UNDETERMINED: "can't tell", None: "error"}


def load_units(dirs):
    """{(folder the replies were drawn in, checkpoint, model, sample): unit}; a copy names its source in source.json."""
    units = {}
    for d in map(Path, dirs):
        src = d / "source.json"
        folder = json.loads(src.read_text())["copied_from"] if src.exists() else str(d.relative_to(ROOT) if d.is_absolute() else d)
        for line in (d / "units.jsonl").read_text().splitlines():
            u = json.loads(line)
            if u.get("error"):
                continue
            if not u["verdicts"]:
                raise ValueError(f"{d}: {u['checkpoint']} {u['model']} #{u['sample']} is not graded")
            if u.get("grader_read") != "normalized":
                raise ValueError(f"{d}: graded on {u.get('grader_read', 'raw')} text; the labels were made on normalized "
                                 "text (decisions.md 2026-10-05)")
            units[(folder, u["checkpoint"], u["model"], u["sample"])] = u
    return units


def rubric_of(spec):
    """{item id: mi | rp | safety | branch}; branch checks carry no rubric tag."""
    out = {}
    for a in spec.raw["always_on_metrics"] + [c for m in spec.raw["key_moments"] for c in m.get("criteria") or []]:
        tags = [t.split(":", 1)[1] for t in a.get("tags") or [] if t.startswith("rubric:")]
        out[a["id"]] = tags[0] if tags else "branch"
    return out


def reply_key(row):
    return (row["folder"], row["checkpoint"], row["model"], row["sample"])


def pair(rows, units):
    """Each label row with the grader's verdict row for the same reply and check."""
    out = []
    for r in rows:
        u = units.get(reply_key(r))
        if u is None:
            raise ValueError(f"no graded reply for {r['labeler']} {r['tab']} row {r['row']} ({' '.join(map(str, reply_key(r)))})")
        v = next((v for v in u["verdicts"] if v["item"] == r["check_id"]), None)
        if v is None:
            raise ValueError(f"the grader has no {r['check_id']} verdict for {' '.join(map(str, reply_key(r)))}")
        out.append((r, v))
    return out


def outcome(r, v):
    if v["verdict"] is None:
        return "grader_error"
    if v["verdict"] == GATE_UNDETERMINED:
        return "cannot_determine"
    return "agree" if v["verdict"] == r["verdict"] else "disagree"


def kind(r, v):
    if outcome(r, v) == "cannot_determine":
        return "grader cannot_determine"
    if "not_applicable" in (r["verdict"], v["verdict"]):
        return "N/A mismatch"
    return "met vs not met"


def rate(a, n):
    return f"{a}/{n} ({a / n:.0%})" if n else "-"


def compare(rows, units, spec):
    first = [r for r in rows if r["role"] == "first" and r["source"] == "human"]
    spec_rows = [r for r in rows if r["role"] == "first" and r["source"] == "spec"]
    primary, fixed = pair(first, units), pair(spec_rows, units)
    second = {(reply_key(r), r["check_id"]): r for r in rows if r["role"] == "second" and r["source"] == "human"}
    people = [(r, second[(reply_key(r), r["check_id"])], v) for r, v in primary if (reply_key(r), r["check_id"]) in second]
    return primary, fixed, people


def per_item(spec, primary, people, groups):
    by = defaultdict(list)
    for r, v in primary:
        by[r["check_id"]].append((r, v))
    pp = defaultdict(list)
    for a, b, _ in people:
        pp[a["check_id"]].append(a["verdict"] == b["verdict"])
    out = []
    for item in sorted(by, key=lambda i: (groups[i] != "mi", i)):
        rv, o = by[item], Counter(outcome(r, v) for r, v in by[item])
        lenient = sum(grader.passes(spec.items[item], v["verdict"]) is True and grader.passes(spec.items[item], r["verdict"]) is False
                      for r, v in rv)
        strict = sum(grader.passes(spec.items[item], v["verdict"]) is False and grader.passes(spec.items[item], r["verdict"]) is True
                     for r, v in rv)
        out.append({"item": item, "rubric": groups[item], "points": spec.items[item].points, "labels": len(rv),
                    "agree": o["agree"], "disagree": o["disagree"], "grader_cannot_determine": o["cannot_determine"],
                    "grader_error": o["grader_error"],
                    "agreement": round(o["agree"] / (o["agree"] + o["disagree"]), 3) if o["agree"] + o["disagree"] else "",
                    "grader_passes_people_fail": lenient, "grader_fails_people_pass": strict,
                    "na_mismatch": sum(kind(r, v) == "N/A mismatch" and outcome(r, v) == "disagree" for r, v in rv),
                    "unsure": sum(r["unsure"] for r, _ in rv),
                    "disagree_when_unsure": sum(r["unsure"] and outcome(r, v) == "disagree" for r, v in rv),
                    "people_pairs": len(pp[item]), "people_agree": sum(pp[item]),
                    "table": " ".join(f"{SHORT[a]}>{SHORT[b]}:{n}" for (a, b), n in
                                      sorted(Counter((r["verdict"], v["verdict"]) for r, v in rv).items(), key=str))})
    return out


def disagreement_rows(primary, fixed, groups):
    out = []
    for r, v in primary + fixed:
        spec_row = r["source"] == "spec"
        if outcome(r, v) == "agree" or (spec_row and v["verdict"] == "not_applicable"):
            continue
        out.append({"checkpoint": r["checkpoint"], "item": r["check_id"], "rubric": groups[r["check_id"]],
                    "kind": "spec says N/A" if spec_row else kind(r, v), "model": r["model"], "sample": r["sample"],
                    "reply_id": r["reply_id"], "labeler": r["labeler"], "label": SHORT[r["verdict"]],
                    "label_na": r["na_code"] or "", "unsure": "unsure" if r["unsure"] else "", "label_quote": r["quote"] or "",
                    "label_why": r["why"] or "", "corrected": r["corrected"] or "", "grader": SHORT[v["verdict"]],
                    "grader_na": v.get("na_code") or "", "grader_quote": v.get("quote") or "",
                    "grader_reasoning": v.get("reasoning") or v.get("error") or "", "cause": "", "change_made": ""})
    return out


def summary(primary, fixed, people, items, groups, n_units):
    def agree_line(pairs):
        o = Counter(outcome(r, v) for r, v in pairs)
        return rate(o["agree"], o["agree"] + o["disagree"]), o
    total, o = agree_line(primary)
    lines = ["# Grader vs the Oct 4 labels", "",
             f"{len(primary)} first-coder labels on {len({reply_key(r) for r, _ in primary})} replies ({n_units} graded "
             "replies loaded). The grader read the same normalized text the labelers saw.", "",
             f"**Grader agrees with the first coder on {total}** of the labels it gave a verdict on. "
             f"Cannot-determine on the gate: {o['cannot_determine']}. Grader errors: {o['grader_error']}.", "",
             "| Rubric | Grader vs first coder | Without unsure rows |", "|---|---|---|"]
    for g in sorted({groups[r["check_id"]] for r, _ in primary}):
        mine = [(r, v) for r, v in primary if groups[r["check_id"]] == g]
        lines.append(f"| {g} | {agree_line(mine)[0]} | {agree_line([(r, v) for r, v in mine if not r['unsure']])[0]} |")
    lean = sum(i["grader_passes_people_fail"] for i in items), sum(i["grader_fails_people_pass"] for i in items)
    lines += ["", f"Where they split on pass or fail, the grader gave credit people did not {lean[0]} times and withheld "
              f"credit people gave {lean[1]} times.", "",
              "## Double-coded replies", "",
              "On the replies two people labeled, the same rows three ways. Second codes by: "
              + ", ".join(f"{who} {n}" for who, n in Counter(b["labeler"] for _, b, _ in people).most_common()) + ".", "",
              "| Pair | Agreement |", "|---|---|"]
    if people:
        same = lambda xs: rate(sum(xs), len(xs))  # noqa: E731
        decided = [(a, b, v) for a, b, v in people if v["verdict"] not in (None, GATE_UNDETERMINED)]
        lines += [f"| first coder vs second coder | {same([a['verdict'] == b['verdict'] for a, b, _ in people])} |",
                  f"| grader vs first coder | {same([v['verdict'] == a['verdict'] for a, _, v in decided])} |",
                  f"| grader vs second coder | {same([v['verdict'] == b['verdict'] for _, b, v in decided])} |"]
    fixed_na = sum(v["verdict"] == "not_applicable" for _, v in fixed)
    lines += ["", "## AO4 where the spec fixes N/A", "",
              f"The grader said N/A on {rate(fixed_na, len(fixed))} of the replies at KM1, KM2, KM5 and KM6, where she "
              "asks nothing. These are not counted above.", "", "## AO3 by how sure the labeler was", "",
              "| First coder | Rows | Grader agrees |", "|---|---|---|"]
    ao3 = [(r, v) for r, v in primary if r["check_id"] == "AO3"]
    for (verdict, unsure), rv in sorted(_group(ao3, lambda r: (r["verdict"], r["unsure"])).items(), key=str):
        lines.append(f"| {SHORT[verdict]}{' (unsure)' if unsure else ''} | {len(rv)} | {agree_line(rv)[0]} |")
    gate = [(r, v) for r, v in primary if r["check_id"] == "KM7-G1"]
    lines += ["", "## KM7 gate (KM7-G1)", "", "| First coder | Grader | Rows |", "|---|---|---|"]
    for (a, b), n in sorted(Counter((SHORT[r["verdict"]] + (" (unsure)" if r["unsure"] else ""), SHORT[v["verdict"]])
                                    for r, v in gate).items()):
        lines.append(f"| {a} | {b} | {n} |")
    lines += ["", "## By item", "", "| Item | Rubric | Grader vs first coder | Grader gives credit people did not "
              "| Grader withholds credit | People agree |", "|---|---|---|---|---|---|"]
    for i in items:
        lines.append(f"| {i['item']} | {i['rubric']} | {rate(i['agree'], i['agree'] + i['disagree'])} "
                     f"| {i['grader_passes_people_fail']} | {i['grader_fails_people_pass']} | {rate(i['people_agree'], i['people_pairs'])} |")
    lines += ["", "Every disagreement is in disagreements.primary.csv, with the label's line and reason beside the "
              "grader's, and blank cause and change-made columns to fill in (design section 05)."]
    return "\n".join(lines) + "\n"


def scores(spec, units):
    """The pilot's own summary over every graded reply, after a line per model for the main line and the branches."""
    us = list(units.values())
    models = sorted({u["model"] for u in us})
    carried = {u["checkpoint"] for u in us} - set(lb.MAIN)
    head = ["# Scores on the graded replies", "", "The points score per model (0-1, clipped mean over replies). The grader "
            "read normalized text (decisions.md 2026-10-05).", "", "| Model | All replies | Main line | Carried branches |",
            "|---|---|---|---|"]
    fmt = lambda xs: "-" if clipped_mean(xs) is None else f"{clipped_mean(xs):.2f} ({len(xs)})"  # noqa: E731
    for m in models:
        mine = [u["score"] for u in us if u["model"] == m and u["score"] is not None]
        main = [u["score"] for u in us if u["model"] == m and u["score"] is not None and u["checkpoint"] not in carried]
        rest = [u["score"] for u in us if u["model"] == m and u["score"] is not None and u["checkpoint"] in carried]
        head.append(f"| {m} | {fmt(mine)} | {fmt(main)} | {fmt(rest)} |")
    body, _ = pilot.summarize(spec, sorted(us, key=lambda u: (u["checkpoint"] in carried, u["checkpoint"], u["model"], u["sample"])),
                              models)
    return "\n".join(head) + "\n\n" + body


def _group(pairs, key):
    out = defaultdict(list)
    for r, v in pairs:
        out[key(r)].append((r, v))
    return out


def write_csv(path, rows):
    with path.open("w", newline="") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("runs", nargs="+", type=Path, help="graded run folders")
    p.add_argument("--labels", type=Path, default=ROOT / "runs/oct4-labels/labels.jsonl")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)
    spec, _ = lb.load_spec()
    groups = rubric_of(spec)
    try:
        units = load_units(a.runs)
        rows = [json.loads(x) for x in a.labels.read_text().splitlines()]
        primary, fixed, people = compare(rows, units, spec)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    items = per_item(spec, primary, people, groups)
    a.out.mkdir(parents=True, exist_ok=True)
    write_csv(a.out / "per_item.csv", items)
    write_csv(a.out / "disagreements.primary.csv", disagreement_rows(primary, fixed, groups))
    write_csv(a.out / "people_disagreements.csv", [
        {"checkpoint": x["checkpoint"], "item": x["check_id"], "model": x["model"], "sample": x["sample"],
         "first": x["labeler"], "first_label": SHORT[x["verdict"]] + (" (unsure)" if x["unsure"] else ""),
         "first_why": x["why"] or x["quote"] or "", "second": y["labeler"],
         "second_label": SHORT[y["verdict"]] + (" (unsure)" if y["unsure"] else ""), "second_why": y["why"] or y["quote"] or "",
         "grader": SHORT[v["verdict"]]} for x, y, v in people if x["verdict"] != y["verdict"]])
    (a.out / "summary.md").write_text(summary(primary, fixed, people, items, groups, len(units)))
    (a.out / "scores.md").write_text(scores(spec, units))
    print(f"wrote {a.out}: {len(primary)} labels compared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
