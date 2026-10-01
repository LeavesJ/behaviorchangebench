"""The hard-question pilot: each branch, each model, a few replies, every check graded, the points score.

    python -m bcb.pilot --dry-run                                   # check the branches file, write every model input
    python -m bcb.pilot --references --grader deepseek:deepseek-flash   # grade each branch's own reference and near-miss replies
    python -m bcb.pilot --conversation <step-1 draft> --grader deepseek:deepseek-flash \\
        --models openrouter:anthropic/claude-opus-5.5,openrouter:openai/gpt-6-astra,openrouter:google/gemini-3.1-pro-preview
    python -m bcb.pilot ... --only HQ-ZYN,KM7-P --samples 2 --resume runs/hard-pilot/<dir>

Writes units.jsonl (one line per checkpoint x model x sample: the reply and every verdict),
scores.csv, summary.md and manifest.json under runs/hard-pilot/. A model error or a reply cut
off at the token limit is recorded and never graded. A grader error is retried once, then
leaves that item out of the reply's score, and the summary counts it.

The pilot picks which hard questions to keep, so its replies are never reported as results:
the Oct run draws fresh samples (hard-queries digest, section 4).
"""

import argparse
import csv
import fnmatch
import hashlib
import json
import statistics
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import branches as br, client, grader
from .item_tests import DEFAULT_SPEC, SAMPLING
from .score import clipped_mean, reply_score
from .spec import Spec, load_conversation

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BRANCHES = ROOT / "trajectories/vaping-v0/hard-pilot.yaml"
MAX_TOKENS = 16000  # high enough that hidden reasoning never truncates a reply (spec run.sampling)
REFERENCE_MODELS = ("reference", "near_miss")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def grade(spec, checkpoint, item_id, reply, target, call=client.chat):
    """One verdict row; a grader error is retried once, then reported, never scored as a fail."""
    item = spec.items[item_id]
    prompt = grader.render_prompt(spec, checkpoint, item, reply)
    errors = []
    for attempt in (1, 2):
        try:
            resp = call(target, [{"role": "user", "content": prompt}], **SAMPLING)
            v = grader.parse(resp["text"], item, reply)
        except (client.CallError, grader.GradeError) as e:
            errors.append(f"attempt {attempt}: {e}")
            continue
        return {"item": item_id, "verdict": v.verdict, "passes": grader.passes(item, v.verdict), "quote": v.quote,
                "reasoning": v.reasoning, "na_code": v.na_code, "usage": resp["usage"], "error": None}
    return {"item": item_id, "verdict": None, "passes": None, "error": "; ".join(errors)}


def score_unit(spec, unit):
    unit["score"] = reply_score([(spec.items[v["item"]], v["verdict"]) for v in unit["verdicts"]])
    return unit


def run_unit(spec, checkpoint, model, sample, grader_target, call=client.chat, reply=None):
    """Get one reply (or take the given one) and grade it on every item at the checkpoint."""
    unit = {"checkpoint": checkpoint, "model": model, "sample": sample, "reply": reply, "error": None,
            "verdicts": [], "score": None}
    if reply is None:
        try:
            r = call(model, spec.messages(checkpoint), temperature=None, max_tokens=MAX_TOKENS)
        except client.CallError as e:
            unit["error"] = f"model: {e}"
            return unit
        unit.update(reply=r["text"], returned_model=r["model"], usage=r["usage"], latency_s=r["latency_s"],
                    finish_reason=r.get("finish_reason"))
        if r.get("finish_reason") == "length":
            unit["error"] = f"model: cut off at max_tokens={MAX_TOKENS}"
            return unit
    unit["verdicts"] = [grade(spec, checkpoint, i, unit["reply"], grader_target, call) for i in spec.items_at(checkpoint)]
    return score_unit(spec, unit)


def _hard_items(spec, checkpoint):
    return [i for i in spec.items_at(checkpoint) if not i.startswith("AO")]


def summarize(spec, units, models):
    """The per-model score, the per-checkpoint scores and every hard check's pass counts, as Markdown."""
    ok = [u for u in units if not u["error"]]
    checkpoints = list(dict.fromkeys(u["checkpoint"] for u in units))
    lines = ["## Score by model", "", "| Model | Score (0-1) | Replies scored | Median words |", "|---|---|---|---|"]
    best = None
    for m in models:
        mine = [u for u in ok if u["model"] == m]
        s = clipped_mean([u["score"] for u in mine])
        words = statistics.median([len(u["reply"].split()) for u in mine]) if mine else "-"
        lines.append(f"| {m} | {'-' if s is None else f'{s:.2f}'} | {sum(u['score'] is not None for u in mine)} | {words} |")
        if s is not None and (best is None or s > best[1]):
            best = (m, s)
    if best:
        lines += ["", f"**Best model: {best[0]} at {best[1]:.2f}** (target: about 0.60 or less)."]
    lines += ["", "## Score by checkpoint", "",
              "Mean reply score per model, clipped to 0-1, with the replies' own scores in brackets.", "",
              "| Checkpoint | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]
    for cp in checkpoints:
        cells = []
        for m in models:
            sc = [u["score"] for u in ok if u["checkpoint"] == cp and u["model"] == m]
            mean_s = clipped_mean(sc)
            cells.append("-" if mean_s is None else f"{mean_s:.2f} ({', '.join('-' if x is None else f'{x:.2f}' for x in sc)})")
        lines.append(f"| {cp} | " + " | ".join(cells) + " |")
    lines += ["", "## Hard checks", "",
              "Passes over replies graded, per model. A pass on an undesired check means the bad behavior was absent.", "",
              "| Check | Points | " + " | ".join(models) + " | Criterion |", "|---|---|" + "---|" * len(models) + "---|"]
    for cp in checkpoints:
        for i in _hard_items(spec, cp):
            item, cells = spec.items[i], []
            for m in models:
                vs = [v for u in ok if u["checkpoint"] == cp and u["model"] == m for v in u["verdicts"] if v["item"] == i]
                graded = [v for v in vs if v["passes"] is not None]
                cells.append(f"{sum(v['passes'] for v in graded)}/{len(graded)}" if graded else "-")
            lines.append(f"| {cp} {i} | {item.points} | " + " | ".join(cells) + f" | {item.criterion} |")
    errs = [u for u in units if u["error"]] + [u for u in ok if any(v["error"] for v in u["verdicts"])]
    lines += ["", "## Errors", ""]
    lines += [f"- {u['checkpoint']} {u['model']} #{u['sample']}: "
              + (u["error"] or "grader error on " + ", ".join(v["item"] for v in u["verdicts"] if v["error"])) for u in errs]
    lines += ["None."] if not errs else []
    return "\n".join(lines) + "\n", best


def _done(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv=None, call=client.chat):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--branches", type=Path, default=DEFAULT_BRANCHES)
    p.add_argument("--conversation", type=Path, help="step 1's draft; without it, earlier assistant turns are beat placeholders")
    p.add_argument("--models", help="comma-separated provider:model targets under test")
    p.add_argument("--grader", help="provider:model; never a family under test (README rule 5)")
    p.add_argument("--only", help="comma-separated checkpoint IDs or patterns; spec checkpoints such as KM7-P work too")
    p.add_argument("--samples", type=int, default=2)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--references", action="store_true", help="grade each branch's reference and near-miss replies only")
    p.add_argument("--dry-run", action="store_true", help="check the branches and write every model input; call nothing")
    p.add_argument("--resume", type=Path, help="an earlier run directory; finished units are kept, the rest are run")
    p.add_argument("--out", type=Path, default=ROOT / "runs/hard-pilot")
    a = p.parse_args(argv)

    try:
        spec = Spec(DEFAULT_SPEC, conversation=load_conversation(a.conversation) if a.conversation else None)
        raw = br.load(a.branches)
        branch_ids = br.attach(spec, raw)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    patterns = a.only.split(",") if a.only else branch_ids
    checkpoints = [c for c in spec.checkpoints(include_optional=True) if any(fnmatch.fnmatch(c, x) for x in patterns)]
    if not checkpoints:
        p.error(f"--only matches no checkpoint: {a.only}")
    if a.references:
        checkpoints = [c for c in checkpoints if c in branch_ids]
        models = list(REFERENCE_MODELS)
    else:
        models = a.models.split(",") if a.models else []
    if not a.dry_run and (not a.grader or not models):
        p.error("--grader and --models (or --references) are required unless --dry-run")
    draft = any(spec.is_draft_prefix(c) for c in checkpoints)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = a.resume or a.out / f"{stamp}-{'references' if a.references else 'dry-run' if a.dry_run else 'models'}"
    out.mkdir(parents=True, exist_ok=True)

    if a.dry_run:
        for c in checkpoints:
            (out / f"{c}.txt").write_text(spec.transcript(c) + "\n")
        print(f"branches ok; wrote {len(checkpoints)} model inputs to {out}"
              + (" (earlier assistant turns are beat placeholders)" if draft else ""))
        print(f"a run would make {len(checkpoints) * len(models) * a.samples} replies and "
              f"{sum(len(spec.items_at(c)) for c in checkpoints) * len(models) * a.samples} grader calls")
        return 0

    units_path, lock = out / "units.jsonl", threading.Lock()
    units = [u for u in _done(units_path) if not u["error"]]
    have = {(u["checkpoint"], u["model"], u["sample"]) for u in units}
    by_id = {b["id"]: b for b in raw["branches"]}
    jobs = [(c, m, s) for c in checkpoints for m in models for s in range(1, (1 if a.references else a.samples) + 1)
            if (c, m, s) not in have]
    units_path.write_text("".join(json.dumps(u) + "\n" for u in units))  # drop errored units so they rerun
    print(f"{len(have)} unit(s) already done; running {len(jobs)}" + (" on a draft prefix" if draft else ""), flush=True)

    def job(c, m, s):
        given = by_id[c][f"{m}_reply"] if a.references else None
        return run_unit(spec, c, m, s, a.grader, call, reply=given)

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(job, *j): j for j in jobs}
        for n, f in enumerate(as_completed(futures), 1):
            u = f.result()
            with lock:
                units.append(u)
                with units_path.open("a") as fh:
                    fh.write(json.dumps(u) + "\n")
            shown = u["error"] or ("no score" if u["score"] is None else f"{u['score']:.2f}")
            print(f"[{n}/{len(jobs)}] {u['checkpoint']} {u['model']} #{u['sample']}: {shown}", flush=True)

    units.sort(key=lambda u: (checkpoints.index(u["checkpoint"]) if u["checkpoint"] in checkpoints else 99,
                              models.index(u["model"]) if u["model"] in models else 99, u["sample"]))
    with (out / "scores.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["checkpoint", "model", "sample", "score", "words", "error"])
        for u in units:
            w.writerow([u["checkpoint"], u["model"], u["sample"], "" if u["score"] is None else round(u["score"], 3),
                        len((u["reply"] or "").split()), u["error"] or ""])
    body, best = summarize(spec, units, models)
    cost = sum((u.get("usage") or {}).get("cost") or 0 for u in units)
    grader_tokens = {k: sum((v.get("usage") or {}).get(k, 0) or 0 for u in units for v in u["verdicts"])
                     for k in ("prompt_tokens", "completion_tokens")}
    manifest = {"mode": "references" if a.references else "models", "models": models,
                "returned_models": sorted({u["returned_model"] for u in units if u.get("returned_model")}),
                "grader": a.grader, "grader_sampling": SAMPLING, "reply_sampling": {"temperature": "vendor default",
                                                                                    "max_tokens": MAX_TOKENS},
                "prompt": {"version": grader.PROMPT_VERSION, "sha": grader.prompt_hash()},
                "spec": {"id": spec.raw["spec"]["id"], "version": spec.version, "sha": _sha(DEFAULT_SPEC)},
                "branches": {"path": str(a.branches.relative_to(ROOT)) if a.branches.is_relative_to(ROOT) else str(a.branches),
                             "sha": _sha(a.branches), "version": raw.get("pilot", {}).get("version")},
                "conversation": {"path": str(a.conversation), "sha": _sha(a.conversation)} if a.conversation else None,
                "draft_prefix": draft, "checkpoints": checkpoints, "samples": a.samples,
                "started": stamp, "finished": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "model_cost_usd_reported": round(cost, 4), "grader_tokens": grader_tokens,
                "best": {"model": best[0], "score": round(best[1], 3)} if best else None}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    head = (f"# Hard-question pilot · {manifest['mode']} · {manifest['finished']}\n\n"
            f"Spec {spec.version} · branches {manifest['branches']['sha']} · grader {a.grader}, prompt "
            f"{grader.PROMPT_VERSION} ({grader.prompt_hash()})" + (" · **draft prefix (beat placeholders)**" if draft else "")
            + f" · model cost reported ${cost:.2f}\n\n")
    (out / "summary.md").write_text(head + body)
    print(f"wrote {out}" + (f"; best {best[0]} {best[1]:.2f}" if best else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
