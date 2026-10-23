"""The hard-question pilot: each branch, each model, a few replies, every check graded, the points score.

    python -m bcb.pilot --dry-run                                   # check the branches file, write every model input
    python -m bcb.pilot --references --grader deepseek:deepseek-flash   # grade each branch's own reference and near-miss replies
    python -m bcb.pilot --conversation <step-1 draft> --grader deepseek:deepseek-flash \\
        --models openrouter:anthropic/claude-opus-5.5,openrouter:openai/gpt-6-astra,openrouter:google/gemini-3.1-pro-preview
    python -m bcb.pilot ... --only HQ-ZYN,KM7-P --samples 2 --resume runs/hard-pilot/<dir>
    python -m bcb.pilot --conversation <frozen> --only 'KM*' --models ... --no-grade --reasoning off --out runs/main-line
    python -m bcb.pilot --conversation <frozen> --only 'KM*' --models ... --grader ... --resume runs/main-line/<dir>
    python -m bcb.pilot --models-config trajectories/vaping-v0/v2/models.yaml --conversation <frozen> --only 'KM*'

Writes units.jsonl (one line per checkpoint x model x sample: the reply and every verdict),
scores.csv, summary.md and manifest.json under runs/hard-pilot/. A model error or a reply cut
off at the token limit is recorded and never graded. A grader error is retried once, then
leaves that item out of the reply's score, and the summary counts it.

--models-config reads the models under test and the graders from a models config, each called with
its own settings and, for a model, its own system prompt (bcb.providers); --models and --grader then
pick from it by name, and a provider:model it does not list is called as before the config. A grader
in the family of any model whose replies the run draws or grades is refused before anything is
called (README rule 5); a draw with --no-grade is checked against every grader in the config. The
manifest records what each model was sent (model_settings).

--no-grade draws the replies and stops, so people can label them before any grader verdict or
score exists; --resume with --grader later grades those replies without drawing new ones.
--reasoning pins hidden reasoning on or off for every model under test; without it each model's
models-config setting applies, or else the vendor's default. Either way the manifest records the
setting. --grade-only makes a resume stop before any call if it would draw a reply, and --normalize
grades the text labelers saw (bcb.labels.normalize) while the unit keeps the raw reply.
--retry-errors asks the grader again for the verdicts that errored on a resume, those items only.

The pilot picks which hard questions to keep, so its replies are never reported as results:
the Oct run draws fresh samples (hard-queries digest, section 4).
"""

import argparse
import csv
import fnmatch
import hashlib
import json
import random
import statistics
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import branches as br, client, grader, labels, models_config, providers
from .item_tests import DEFAULT_SPEC
from .providers import REASONING, SAMPLING, sampling
from .score import clipped_mean, reply_score
from .spec import Spec, load_conversation

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BRANCHES = ROOT / "trajectories/vaping-v0/hard-pilot.yaml"
MAX_TOKENS = providers.REPLY_MAX_TOKENS
REFERENCE_MODELS = ("reference", "near_miss")
BOOTSTRAP_DRAWS = 2000


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def grade(spec, checkpoint, item_id, reply, target, call=client.chat, settings=SAMPLING):
    """One verdict row; a grader error is retried once, then reported, never scored as a fail."""
    item = spec.items[item_id]
    prompt = grader.render_prompt(spec, checkpoint, item, reply)
    errors = []
    for attempt in (1, 2):
        try:
            resp = call(target, [{"role": "user", "content": prompt}], **settings)
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


def run_unit(spec, checkpoint, model, sample, grader_target, call=client.chat, reply=None, thinking=None,
             settings=SAMPLING, normalize=False, entry=None):
    """Get one reply (or take the given one) and grade it on every item at the checkpoint.

    `entry`, the model's models-config entry (bcb.providers), gives its settings and system prompt; without one the
    model is called as before the config: the vendor's temperature, max_tokens 16000 and `thinking`.
    With grader_target None the reply is drawn and left ungraded, for labeling before any verdict exists.
    """
    unit = {"checkpoint": checkpoint, "model": model, "sample": sample, "reply": reply, "error": None,
            "verdicts": [], "score": None}
    if reply is None:
        sent = providers.reply_settings(entry) if entry else {"temperature": None, "max_tokens": MAX_TOKENS,
                                                              "thinking": thinking}
        try:
            r = call(model, providers.messages(entry, spec.messages(checkpoint)), **sent)
        except client.CallError as e:
            unit["error"] = f"model: {e}"
            return unit
        unit.update(reply=r["text"], returned_model=r["model"], usage=r["usage"], latency_s=r["latency_s"],
                    finish_reason=r.get("finish_reason"))
        if r.get("finish_reason") == "length":
            limit = sent["max_tokens"]
            unit["error"] = f"model: cut off at max_tokens={limit}" if limit else "model: cut off at the vendor's limit"
            return unit
    return grade_unit(spec, unit, grader_target, call, settings, normalize) if grader_target else unit


def grade_unit(spec, unit, grader_target, call=client.chat, settings=SAMPLING, normalize=False):
    """Grade a unit that already holds its reply, keeping everything else it records."""
    text = labels.normalize(unit["reply"]) if normalize else unit["reply"]
    unit["verdicts"] = [grade(spec, unit["checkpoint"], i, text, grader_target, call, settings)
                        for i in spec.items_at(unit["checkpoint"])]
    unit["grader_read"] = "normalized" if normalize else "raw"
    return score_unit(spec, unit)


def regrade_errors(spec, unit, grader_target, call=client.chat, settings=SAMPLING):
    """Ask again for the items whose verdict errored, reading the text the unit was graded on; the rest stay."""
    text = labels.normalize(unit["reply"]) if unit.get("grader_read") == "normalized" else unit["reply"]
    unit["verdicts"] = [grade(spec, unit["checkpoint"], v["item"], text, grader_target, call, settings) if v["error"] else v
                        for v in unit["verdicts"]]
    return score_unit(spec, unit)


def subscore(spec, unit, core):
    """The points score over the always-on items only (core) or over the rest (scenario), as spec rubrics.scoring asks."""
    return reply_score([(spec.items[v["item"]], v["verdict"]) for v in unit["verdicts"]
                        if v["item"].startswith("AO") == core])


def clustered_interval(by_checkpoint, draws=BOOTSTRAP_DRAWS, seed=0):
    """95% percentile bootstrap of the clipped mean, resampling whole checkpoints, since a checkpoint's replies share a prefix."""
    groups = [[s for s in scores if s is not None] for scores in by_checkpoint.values()]
    groups = [g for g in groups if g]
    if not groups:
        return None
    rng = random.Random(seed)
    means = sorted(clipped_mean([s for g in (rng.choice(groups) for _ in groups) for s in g]) for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def _reasoning_tokens(unit):
    return ((unit.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") or 0


def _hard_items(spec, checkpoint):
    return [i for i in spec.items_at(checkpoint) if not i.startswith("AO")]


def summarize(spec, units, models):
    """The per-model score, the per-checkpoint scores and every hard check's pass counts, as Markdown."""
    ok = [u for u in units if not u["error"]]
    checkpoints = list(dict.fromkeys(u["checkpoint"] for u in units))
    fmt = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
    lines = ["## Score by model", "",
             "Core is the always-on items alone and Scenario the rest, reported apart (spec rubrics.scoring). The interval "
             "resamples whole checkpoints.", "",
             "| Model | Score (0-1) | Core (always-on) | Scenario | 95% CI by checkpoint | Replies scored | Median words "
             "| Median reasoning tokens |", "|---|---|---|---|---|---|---|---|"]
    best = None
    for m in models:
        mine = [u for u in ok if u["model"] == m]
        s = clipped_mean([u["score"] for u in mine])
        core = clipped_mean([subscore(spec, u, core=True) for u in mine])
        scenario = clipped_mean([subscore(spec, u, core=False) for u in mine])
        ci = clustered_interval({cp: [u["score"] for u in mine if u["checkpoint"] == cp] for cp in checkpoints})
        words = statistics.median([len(u["reply"].split()) for u in mine]) if mine else "-"
        thought = statistics.median([_reasoning_tokens(u) for u in mine]) if mine else "-"
        lines.append(f"| {m} | {fmt(s)} | {fmt(core)} | {fmt(scenario)} | {'-' if ci is None else f'{ci[0]:.2f}-{ci[1]:.2f}'} "
                     f"| {sum(u['score'] is not None for u in mine)} | {words} | {thought} |")
        if s is not None and (best is None or s > best[1]):
            best = (m, s)
    if best:
        lines += ["", f"**Best model: {best[0]} at {best[1]:.2f}** (target: about 0.60 or less)."]
    elif ok:
        lines += ["", "Replies drawn and not graded yet (--no-grade): no verdict or score exists."]
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
    lines += ["", "## Checks", "",
              "Passes over replies graded, per model, always-on items left out. A pass on an undesired check means the bad "
              "behavior was absent.", "",
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


def _refuse(lines, why):
    """Say why the run stops before any call, and stop it."""
    print("\n".join(lines + ([why] if why else []) + ["Nothing was called."]), file=sys.stderr)
    return 2


def _done(path):
    """The units on disk, one per reply: a reply graded on resume is appended after its ungraded line, and the later line wins."""
    if not path.exists():
        return []
    latest = {}
    for line in path.read_text().splitlines():
        if line.strip():
            u = json.loads(line)
            latest[(u["checkpoint"], u["model"], u["sample"])] = u
    return list(latest.values())


def main(argv=None, call=client.chat):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--branches", type=Path, default=DEFAULT_BRANCHES)
    p.add_argument("--conversation", type=Path, help="step 1's draft; without it, earlier assistant turns are beat placeholders")
    p.add_argument("--models-config", type=Path, help="a models config: the models under test and the graders, each "
                                                       "with its own settings (bcb.providers)")
    p.add_argument("--models", help="comma-separated models under test: names the models config gives, or "
                                    "provider:model targets (default: every model in the models config)")
    p.add_argument("--grader", help="a grader the models config names, or provider:model (default: the config's "
                                    "primary); never a family under test (README rule 5)")
    p.add_argument("--only", help="comma-separated checkpoint IDs or patterns; spec checkpoints such as KM7-P work too")
    p.add_argument("--samples", type=int, default=2)
    p.add_argument("--no-grade", action="store_true", help="draw replies only; grade later with --resume and --grader")
    p.add_argument("--reasoning", choices=sorted(REASONING),
                   help="hidden reasoning for every model under test, over the models config (default: each model's "
                        "setting there, or the vendor's); recorded in the manifest")
    p.add_argument("--grader-reasoning", choices=sorted(REASONING),
                   help="the grader's hidden reasoning (some graders refuse it off), over the models config (default "
                        "off for a provider:model it does not list); recorded in the manifest")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--references", action="store_true", help="grade each branch's reference and near-miss replies only")
    p.add_argument("--dry-run", action="store_true", help="check the branches and write every model input; call nothing")
    p.add_argument("--resume", type=Path, help="an earlier run directory; finished units are kept, the rest are run")
    p.add_argument("--grade-only", action="store_true",
                   help="with --resume: grade the replies on disk and stop before any call if one would be drawn")
    p.add_argument("--retry-errors", action="store_true",
                   help="with --resume: ask the grader again for verdicts that errored, those items only")
    p.add_argument("--normalize", action="store_true",
                   help="grade the reply as labelers saw it (bcb.labels.normalize); the unit keeps the raw reply")
    p.add_argument("--out", type=Path, default=ROOT / "runs/hard-pilot")
    a = p.parse_args(argv)

    try:
        spec = Spec(DEFAULT_SPEC, conversation=load_conversation(a.conversation) if a.conversation else None)
        raw = br.load(a.branches)
        branch_ids = br.attach(spec, raw)
        config = models_config.load(a.models_config) if a.models_config else None
    except ValueError as e:  # a spec, branches file or models config with problems
        print(e, file=sys.stderr)
        return 2
    patterns = a.only.split(",") if a.only else branch_ids
    checkpoints = [c for c in spec.checkpoints(include_optional=True) if any(fnmatch.fnmatch(c, x) for x in patterns)]
    if not checkpoints:
        p.error(f"--only matches no checkpoint: {a.only}")
    if a.no_grade and (a.references or a.grader):
        p.error("--no-grade draws replies without grading; it takes neither --references nor --grader")
    try:  # every model and the grader, each with its settings (bcb.providers)
        entries = [] if a.references else providers.models_for_run(config, a.models.split(",") if a.models else None,
                                                                    a.reasoning)
        judge = None if a.no_grade else providers.grader_for_run(config, a.grader, a.grader_reasoning)
    except ValueError as e:
        p.error(str(e))
    if a.references:
        checkpoints = [c for c in checkpoints if c in branch_ids]
        models = list(REFERENCE_MODELS)
    else:
        models = [m.target for m in entries]
    if ((a.grade_only or a.retry_errors) and not (a.resume and judge)) or (a.normalize and not judge):
        p.error("--grade-only and --retry-errors need --resume and a grader; --normalize needs a grader (--grader, or a "
                "models config with a primary grader)")
    if not a.dry_run and (not (judge or a.no_grade) or not models):
        p.error("a grader (--grader, or a models config with a primary) or --no-grade, and models (--models, a models "
                "config, or --references) are required unless --dry-run")
    # README rule 5, before anything is called; a draw is checked against every grader that may grade it later
    refused = providers.same_family([judge] if judge else list(config.graders.values()) if config else [], entries)
    if refused:
        return _refuse(refused, None if judge else f"This run only draws replies, but the graders in "
                                                   f"{a.models_config} are the ones that will grade them.")
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
    if a.grade_only and jobs:
        print(f"--grade-only: {len(jobs)} checkpoint-model-sample combination(s) are not in {units_path}, so a resume "
              f"would draw them (first: {' '.join(map(str, jobs[0]))}); nothing was called", file=sys.stderr)
        return 2
    # a run drawn with --no-grade is graded on its own replies when resumed with a grader; nothing new is drawn for it
    ungraded = [] if a.no_grade else [u for u in units if not u["verdicts"]]
    retry = [u for u in units if a.retry_errors and u["verdicts"] and any(v["error"] for v in u["verdicts"])]
    earlier = [] if a.references else [providers.model_under_test(t, config) for t in dict.fromkeys(
        u["model"] for u in ungraded + retry) if t not in models and t not in REFERENCE_MODELS]
    refused = providers.same_family([judge] if judge else [], earlier)
    if refused:  # replies drawn earlier, for a model this run does not name, that this run's grader would grade
        return _refuse(refused, f"Those replies are already in {units_path}, and this run would grade them.")
    units = [u for u in units if u not in ungraded and u not in retry]
    # errored units are dropped and rerun; ungraded replies stay on disk until their graded line is appended after them
    units_path.write_text("".join(json.dumps(u) + "\n" for u in units + ungraded + retry))
    print(f"{len(units)} unit(s) already done; grading {len(ungraded)} drawn earlier; running {len(jobs)}"
          + (f"; asking again for {sum(bool(v['error']) for u in retry for v in u['verdicts'])} errored verdict(s) on "
             f"{len(retry)} replies" if a.retry_errors else "")
          + (" on a draft prefix" if draft else ""), flush=True)
    target = judge.target if judge else None
    settings = providers.grader_settings(judge) if judge else sampling(a.grader_reasoning or "off")
    by_target = {m.target: m for m in entries}

    def job(c, m, s):
        given = by_id[c][f"{m}_reply"] if a.references else None
        return run_unit(spec, c, m, s, target, call, reply=given, settings=settings, normalize=a.normalize,
                        entry=by_target.get(m))

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(job, *j): j for j in jobs}
        futures.update({pool.submit(grade_unit, spec, u, target, call, settings, a.normalize): u for u in ungraded})
        futures.update({pool.submit(regrade_errors, spec, u, target, call, settings): u for u in retry})
        jobs = jobs + ungraded + retry
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
    units_path.write_text("".join(json.dumps(u) + "\n" for u in units))  # compact: one line per reply
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
                "grader": target, "graded": all(u["verdicts"] for u in units if not u["error"]),
                "grader_read": sorted({u.get("grader_read", "raw") for u in units if u["verdicts"]}),
                "grader_sampling": settings,
                # as runs have always recorded it; null when the models were sent different settings
                "reply_sampling": providers.reply_sampling(entries) if entries else
                {"temperature": "vendor default", "max_tokens": MAX_TOKENS, "reasoning": a.reasoning or "vendor default"},
                "models_config": providers.config_record(config), "grader_name": judge.name if judge else None,
                "model_settings": {m.target: providers.model_record(m, config) for m in entries},
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
    title = (f"Pilot {raw.get('pilot', {}).get('id', a.branches.stem)}" if set(checkpoints) & set(branch_ids)
             else "Main line")
    head = (f"# {title} · {manifest['mode']} · {manifest['finished']}\n\n"
            f"Spec {spec.version} · branches {manifest['branches']['sha']} · grader {target}, prompt "
            f"{grader.PROMPT_VERSION} ({grader.prompt_hash()})" + (" · **draft prefix (beat placeholders)**" if draft else "")
            + f" · model cost reported ${cost:.2f}\n\n")
    (out / "summary.md").write_text(head + body)
    print(f"wrote {out}" + (f"; best {best[0]} {best[1]:.2f}" if best else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
