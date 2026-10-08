"""The disagreement review: every split between the grader and the first coder, judged by a teammate who did not label it.

    python -m bcb.review --out runs/oct8-graded/review

The Oct 8 prototype reports every grader-vs-label disagreement with its cause and the change it calls for (spec
labels.disagreement_table, design section 05). Each disagreement goes to someone who was not its first coder,
grouped by checkpoint so the chat is read once. Packets and Sheets name neither the model nor the labeler: a row ID
(D001, ...) maps back through review-key.json, which stays in runs/. Writes, per person, a Sheet and one packet
(HTML) per checkpoint, plus the key.
"""

import argparse
import hashlib
import html
import json
import random
import re
import sys
from pathlib import Path

from . import compare, labels as lb

ROOT = Path(__file__).resolve().parent.parent
GRADED = [ROOT / "runs/oct8-graded" / d for d in ("main-astra-20261002T094617Z", "main-gemini-grok-20261002T091818Z",
                                                  "carry-20261002T172455Z")]
ORDER = lb.MAIN + ["MS-DECIDE", "MS-WHOLEPLAN", "MS-LAPSE"]
# nobody reviews a checkpoint they first-coded; Jarron second-coded the main line, so he takes two branches
PLAN = {"Vincent": ["KM5", "KM6"], "Ryan": ["KM7", "KM7-T", "KM7-P", "MS-DECIDE"], "Emily": ["KM2", "KM4", "KM8"],
        "Tyler": ["KM1", "KM3"], "Jarron": ["MS-LAPSE", "MS-WHOLEPLAN"]}
PEOPLE = re.compile(r"\b(Vincent|Ryan|Emily|Tyler|Jarron)\b")
# the reviewer's call, as the Sheet's dropdown shows it, and the design section 05 cause it records
CALLS = {"label right: grader misread": "grader error",
         "grader right: label missed it": "manual error",
         "both fair: wording allows both": "item wording ambiguous",
         "close call: wording is clear": "genuinely borderline",
         "neither: see why": "both wrong"}
WORDING = "both fair: wording allows both"
SAY = {"met": "yes", "not_met": "no", "not_applicable": "doesn't apply", "cannot_determine": "can't tell", None: "no verdict"}


def disagreements(spec, units, rows, rng):
    """Each disagreement with its label row, grader verdict and reply, checkpoints in order, replies shuffled within."""
    primary, fixed, _ = compare.compare(rows, units, spec)
    found = [{"label": r, "grader": v, "spec_row": r["source"] == "spec", "unit": units[compare.reply_key(r)]}
             for r, v in primary + fixed
             if compare.outcome(r, v) != "agree" and not (r["source"] == "spec" and v["verdict"] == "not_applicable")]
    replies = {}
    for d in found:
        replies.setdefault(d["label"]["checkpoint"], []).append(compare.reply_key(d["label"]))
    place = {}
    for cp, keys in replies.items():
        keys = sorted(set(keys))
        rng.shuffle(keys)  # replies from one model never sit together by construction
        place.update({k: i for i, k in enumerate(keys, 1)})
    for d in found:
        d["reply_n"] = place[compare.reply_key(d["label"])]
    found.sort(key=lambda d: (ORDER.index(d["label"]["checkpoint"]), d["reply_n"],
                              spec.items_at(d["label"]["checkpoint"]).index(d["label"]["check_id"])))
    return found


def assign(found):
    """{person: [(checkpoint, [disagreement, ...]), ...]} by PLAN, with row IDs; refuses anyone's own first codes."""
    owner = {cp: p for p, cps in PLAN.items() for cp in cps}
    missing = {d["label"]["checkpoint"] for d in found} - set(owner)
    if missing or len(owner) != sum(map(len, PLAN.values())):
        raise ValueError(f"every checkpoint needs exactly one reviewer: {sorted(missing) or 'one is listed twice'}")
    out = {p: [(cp, [d for d in found if d["label"]["checkpoint"] == cp]) for cp in cps] for p, cps in PLAN.items()}
    n = 0
    for p, tabs in out.items():
        for cp, ds in tabs:
            for d in ds:
                if d["label"]["labeler"] == p:
                    raise ValueError(f"{p} would review their own label at {cp} ({d['label']['check_id']})")
                n += 1
                d["id"], d["reviewer"] = f"D{n:03d}", p
    return {p: [(cp, ds) for cp, ds in tabs if ds] for p, tabs in out.items()}


def label_said(d):
    r = d["label"]
    if d["spec_row"]:
        return "doesn't apply (set by the spec: Dani asks nothing here, so nobody labeled this row)"
    parts = [SAY[r["verdict"]] + (" (marked unsure)" if r["unsure"] else "")]
    if r["quote"]:
        parts.append(f'line: "{r["quote"]}"')
    if r["why"]:
        parts.append(f"why: {r['why']}")
    return " · ".join(parts)


def grader_said(d, reasoning=False):
    v = d["grader"]
    parts = [SAY[v["verdict"]]]
    if v.get("quote"):
        parts.append(f'line: "{v["quote"]}"')
    if reasoning and v.get("reasoning"):
        parts.append(f"reasoning: {v['reasoning']}")
    return " · ".join(parts)


def tab_name(i, cp):
    return f"{i} {cp}"


def packet_html(person, i, cp, ds, spec):
    tab, esc = tab_name(i, cp), html.escape
    msgs = spec.messages(cp)
    chat = "".join(f'<div class="turn"><div class="who">{"Dani" if m["role"] == "user" else "Assistant"}</div>'
                   f'<div class="text">{esc(m["content"])}</div></div>' for m in msgs[:-1])
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>Review {esc(tab)} packet</title>"
           f"<style>{lb._CSS}.row {{ border: 1.5pt solid #555; padding: 7pt 10pt; margin: 8pt 0 12pt; }}"
           ".row b.id { font: bold 11pt Helvetica, Arial, sans-serif; } .side { margin: 5pt 0 0; } "
           ".side .k { font: bold 10pt Helvetica, Arial, sans-serif; color: #444; }</style></head><body>",
           f"<h1>Review packet {esc(tab)}</h1>",
           f"<p style='margin:0 0 10pt'><b>{esc(person)}</b> &middot; Sheet tab <b>\"{esc(tab)}\"</b> &middot; "
           f"{len({d['reply_n'] for d in ds})} replies &middot; {len(ds)} rows</p>",
           "<div class='how'><b>How to use this packet</b><ol>"
           "<li>Read the note and the chat below, and Dani's last message (the box) closely.</li>"
           "<li>Each reply below is followed by the rows where a person's label and the grader split. "
           "Read the whole reply first, then each row: the check, what the label said, what the grader said and why.</li>"
           f"<li>In tab <b>\"{esc(tab)}\"</b> of your Sheet, find the same row ID (column A), pick your call in "
           "column G and write one line in H.</li>"
           "<li>Judge the reply against the check's own words, the way a careful reader would. Who labeled it "
           "and which AI wrote it are left out on purpose.</li></ol></div>",
           "<h2>The note (the grader gets the same one)</h2>",
           f"<div class='note'>{esc(spec.grader_note(cp))}</div>",
           "<h2>The chat so far</h2>", chat,
           f"<div class='last'><div class='label'>Dani's last message: every reply below answers this</div>"
           f"<div class='text'>{esc(msgs[-1]['content'])}</div></div>",
           "<h2>The replies and the rows to judge</h2>"]
    for n in sorted({d["reply_n"] for d in ds}):
        mine = [d for d in ds if d["reply_n"] == n]
        out.append(f"<div class='rhead'>Reply {n} <span>({len(mine)} row{'s' if len(mine) > 1 else ''}: "
                   f"{', '.join(d['id'] for d in mine)})</span></div>"
                   f"<div class='reply text'>{esc(lb.normalize(mine[0]['unit']['reply']))}</div>")
        for d in mine:
            item = spec.items[d["label"]["check_id"]]
            out.append(f"<div class='row'><b class='id'>{d['id']} &middot; {item.id} ({esc(lb.polarity_words(item))})</b>"
                       f"<div class='text'>{esc(lb.check_text(item))}</div>"
                       f"<div class='side'><span class='k'>The label said:</span> {esc(label_said(d))}</div>"
                       f"<div class='side'><span class='k'>The grader said:</span> {esc(grader_said(d, reasoning=True))}</div></div>")
    out.append("<p style='margin-top:24pt'><i>End of packet. Go to the next tab on your Start here list.</i></p></body></html>")
    return "".join(out)


def write_sheet(person, tabs, spec, key_sha, path):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    grey, head = PatternFill("solid", fgColor="E6E6E6"), PatternFill("solid", fgColor="333333")
    yellow, red = PatternFill("solid", fgColor="FFF2B3"), PatternFill("solid", fgColor="F8C9C4")
    wrap, top = Alignment(wrap_text=True, vertical="top"), Border(top=Side(style="medium", color="333333"))
    wb = Workbook()
    start = wb.active
    start.title = "Start here"
    headers = ["A. Row", "B. Reply (number in your packet)", "C. Check", "D. Check wording", "E. The label said",
               "F. The grader said (its reasoning is in the packet)", "G. Your call (pick one)", "H. Why (one line)",
               "I. What would fix it (needed for \"both fair\", optional otherwise)", "row_id", "check_id", "reviewer"]
    widths = [8, 10, 14, 70, 44, 40, 30, 44, 44, 8, 10, 10]
    info = []
    for i, (cp, ds) in enumerate(tabs, 1):
        ws = wb.create_sheet(tab_name(i, cp))
        ws.append(headers)
        for col, w in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + col)].width = w
            c = ws.cell(row=1, column=col)
            c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), head, wrap
        ws.row_dimensions[1].height = 48
        ws.freeze_panes = "A2"
        for col in "JKL":
            ws.column_dimensions[col].hidden = True
        dv = DataValidation(type="list", formula1='"' + ",".join(CALLS) + '"', allow_blank=True)
        dv.showErrorMessage = True
        ws.add_data_validation(dv)
        last_reply = None
        for r, d in enumerate(ds, 2):
            item = spec.items[d["label"]["check_id"]]
            ws.append([d["id"], d["reply_n"], f"{item.id}\n({lb.polarity_words(item)})", lb.check_text(item),
                       label_said(d), grader_said(d), None, None, None, d["id"], item.id, person])
            for col in range(1, 10):
                cell = ws.cell(row=r, column=col)
                cell.alignment = wrap
                if col <= 6:
                    cell.fill = grey
                if d["reply_n"] != last_reply:
                    cell.border = top
            last_reply = d["reply_n"]
            dv.add(f"G{r}")
            ws.row_dimensions[r].height = min(15 * max(len(lb.check_text(item)) / 75, len(label_said(d)) / 48, 3), 409)
        last = len(ds) + 1
        ws.conditional_formatting.add(f"G2:G{last}", FormulaRule(formula=['$G2=""'], fill=yellow))
        ws.conditional_formatting.add(f"H2:I{last}", FormulaRule(
            formula=[f'OR(AND($G2<>"",$H2=""),AND($G2="{WORDING}",$I2=""))'], fill=red))
        info.append((ws.title, len({d["reply_n"] for d in ds}), len(ds), last))

    def put(row, values, font=None, fill=None, height=None, merge=False):
        for col, v in enumerate(values, 1):
            c = start.cell(row=row, column=col, value=v)
            c.alignment = wrap
            if font:
                c.font = font
            if fill:
                c.fill = fill
        if merge:
            start.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        if height:
            start.row_dimensions[row].height = height

    start.column_dimensions["A"].width = 34
    for col, w in zip("BCDEF", [10, 10, 16, 16, 18]):
        start.column_dimensions[col].width = w
    put(1, [f"Review - {person}"], Font(bold=True, size=18), height=30)
    put(2, ["Each row is a place where a person's label and the grader split. Your job: say who read the reply "
            "right, or why both readings are fair. Fill in only the white cells (G, H, I). Grey cells are filled in "
            "for you: please don't change them, and don't add, delete or sort rows."], merge=True, height=60)
    put(4, ["What to do"], Font(bold=True, size=13))
    steps = ["1. Open packet 1 and read the note, the chat and Dani's last message.",
             "2. Read a reply in the packet, then each of its rows: the check, what the label said, what the grader said and why.",
             "3. In the tab with the same number, find the row ID in column A and pick your call in G.",
             "4. Write one line in H saying why. If you picked \"both fair\", write in I how the check's wording could "
             "be changed so both would read it the same way.",
             "5. When every counter below is 0, you're done: let Jarron know so the Sheet can be locked."]
    r = 5
    for s in steps:
        put(r, [s], merge=True, height=30)
        r += 1
    r += 1
    put(r, ["The five calls (column G)"], Font(bold=True, size=13))
    r += 1
    meaning = {"label right: grader misread": "The person's label fits the check's words; the grader got this one wrong.",
               "grader right: label missed it": "The grader's answer fits the check's words; the label missed something "
                                                "in the reply (easy to do on a long day).",
               "both fair: wording allows both": "Careful readers could land either way because the check's words are "
                                                 "unclear here. Say in I what would settle it.",
               "close call: wording is clear": "The words are clear, but this reply sits right on the line.",
               "neither: see why": "Both answers are wrong (for example, it should be doesn't apply). Say the right "
                                   "answer in H."}
    for call, m in meaning.items():
        put(r, [f"{call}: {m}"], merge=True, height=30)
        r += 1
    r += 1
    put(r, ["Your tabs, in order", "Replies", "Rows", "Rows with no call", "Calls with no why",
            "\"Both fair\" with no fix"], Font(bold=True), fill=PatternFill("solid", fgColor="DDDDDD"), height=45)
    r += 1
    for title, n_replies, n_rows, last in info:
        q = f"'{title}'!"
        G, H, I = (f"{q}{c}2:{c}{last}" for c in "GHI")
        put(r, [title, n_replies, n_rows, f"=COUNTBLANK({G})", f'=COUNTIFS({G},"<>",{H},"")',
                f'=COUNTIFS({G},"{WORDING}",{I},"")'])
        r += 1
    put(r, ["Total", sum(t[1] for t in info), sum(t[2] for t in info)], Font(bold=True))
    r += 2
    put(r, [f"Key file sha256: {key_sha}"], Font(size=8, color="888888"), merge=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_key(assignment, path):
    rows = {d["id"]: {"reviewer": p, "tab": tab_name(i, cp), "reply_n": d["reply_n"], "folder": d["label"]["folder"],
                      "checkpoint": cp, "model": d["label"]["model"], "sample": d["label"]["sample"],
                      "check_id": d["label"]["check_id"], "labeler": d["label"]["labeler"],
                      "label": d["label"]["verdict"], "grader": d["grader"]["verdict"], "spec_row": d["spec_row"]}
            for p, tabs in assignment.items() for i, (cp, ds) in enumerate(tabs, 1) for d in ds}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"note": "Maps each review row ID to its reply, labeler and grader verdict. "
                                        "Keep out of the review folders.", "rows": rows}, indent=1))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(out, seed=20261006):
    spec, _ = lb.load_spec()
    units = compare.load_units(GRADED)
    rows = [json.loads(x) for x in (ROOT / "runs/oct4-labels/labels.jsonl").read_text().splitlines()]
    assignment = assign(disagreements(spec, units, rows, random.Random(seed)))
    key_sha = write_key(assignment, out / "review-key.json")
    summary = {}
    for person, tabs in assignment.items():
        folder = out / f"Review - {person}"
        write_sheet(person, tabs, spec, key_sha, folder / f"Review - {person}.xlsx")
        for i, (cp, ds) in enumerate(tabs, 1):
            page = packet_html(person, i, cp, ds, spec)
            text = re.sub(r"<[^>]+>", " ", page.split("<h2>The replies", 1)[1])
            for pattern, what in ((lb.MODEL_NAMES, "a model"), (PEOPLE, "a teammate")):
                hit = pattern.search(text)
                if hit:
                    raise ValueError(f"{person} {cp}: packet names {what} ({hit.group(0)})")
            (folder / f"{tab_name(i, cp)} review packet.html").write_text(page, encoding="utf-8")
        summary[person] = {cp: len(ds) for cp, ds in tabs}
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, required=True, help="a folder under runs/ (the key names models and labelers)")
    a = p.parse_args(argv)
    if "runs" not in a.out.resolve().parts:
        p.error("--out must be inside a runs/ folder")
    try:
        summary = build(a.out)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    for person, tabs in summary.items():
        print(f"{person}: {sum(tabs.values())} rows ({', '.join(f'{cp} {n}' for cp, n in tabs.items())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
