"""Grade every item's known-pass and known-fail examples; an item is usable only if its grader gets all of them right.

    python -m bcb.item_tests --grader deepseek:deepseek-flash
    python -m bcb.item_tests --models-config trajectories/vaping-v0/v2/models.yaml   # its primary grader, its settings
    python -m bcb.item_tests --dry-run                # write the prompts, call nothing
    python -m bcb.item_tests --grader ... --only KM4-*,AO3
    python -m bcb.item_tests --grader ... --conversation <step-1 conversation file>   # the real prefix

Writes results.jsonl, summary.md and manifest.json under runs/item-tests/. A grader error
(bad JSON, a `met` with no quote from the reply, a timeout) is retried once and then
reported as an error, never counted as a wrong verdict (spec grading.errors).
"""

import argparse
import fnmatch
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import client, examples as ex_mod, grader, models_config, providers
from .providers import REASONING as GRADER_REASONING, SAMPLING  # a grader's call settings live in bcb.providers
from .spec import Spec, load_conversation

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = ROOT / "trajectories/vaping-v0/spec.yaml"
DEFAULT_EXAMPLES = ROOT / "trajectories/vaping-v0/item-examples.yaml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _selected(item_id: str, only: list[str] | None) -> bool:
    return not only or any(fnmatch.fnmatch(item_id, pattern) for pattern in only)


def grade_one(spec, example, target, call=client.chat, settings=SAMPLING):
    """Grade one example; returns a result row. `call` is injectable so tests never touch the network."""
    item = spec.items[example.item]
    prompt = grader.render_prompt(spec, example.checkpoint, item, example.reply)
    row = {"example": example.id, "item": item.id, "checkpoint": example.checkpoint, "kind": example.kind,
           "expected": example.expected, "verdict": None, "correct": None, "error": None}
    errors = []
    for attempt in (1, 2):
        try:
            resp = call(target, [{"role": "user", "content": prompt}], **settings)
            v = grader.parse(resp["text"], item, example.reply)
        except (client.CallError, grader.GradeError) as e:
            errors.append(f"attempt {attempt}: {e}")
            row["error"] = "; ".join(errors)
            continue
        row.update(verdict=v.verdict, quote=v.quote, reasoning=v.reasoning, na_code=v.na_code,
                   correct=v.verdict == example.expected and (example.kind != "known_na" or v.na_code == example.na_code),
                   error=None, attempts=attempt,
                   model=resp["model"], usage=resp["usage"], latency_s=resp["latency_s"])
        break
    return row


def summarize(spec, rows):
    """Per item: how many examples, right, wrong, errors, and whether the item is usable."""
    by_item = defaultdict(list)
    for r in rows:
        by_item[r["item"]].append(r)
    table = []
    for item_id in spec.items:
        rs = by_item.get(item_id, [])
        if not rs:
            continue
        right = sum(r["correct"] is True for r in rs)
        wrong = [r["example"] for r in rs if r["correct"] is False]
        errors = [r["example"] for r in rs if r["error"]]
        kinds = {r["kind"] for r in rs}
        usable = not wrong and not errors and {"known_pass", "known_fail"} <= kinds
        table.append({"item": item_id, "examples": len(rs), "right": right, "wrong": wrong,
                      "errors": errors, "usable": usable})
    return table


def _summary_md(table, manifest):
    lines = [f"# Item tests · {manifest['grader']} · {manifest['finished']}", "",
             f"Spec {manifest['spec']['id']} {manifest['spec']['version']} · prompt {manifest['prompt']['version']} "
             f"({manifest['prompt']['sha']}) · examples {manifest['examples']['sha']}"
             + (" · **draft prefix (beat placeholders)**" if manifest["draft_prefix"] else ""), "",
             f"**{sum(t['usable'] for t in table)} of {len(table)} items usable.**", "",
             "| Item | Examples | Right | Wrong | Errors | Usable |", "|---|---|---|---|---|---|"]
    for t in table:
        lines.append(f"| {t['item']} | {t['examples']} | {t['right']} | {', '.join(t['wrong']) or '-'} | "
                     f"{', '.join(t['errors']) or '-'} | {'yes' if t['usable'] else '**no**'} |")
    return "\n".join(lines) + "\n"


def main(argv=None, call=client.chat):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    p.add_argument("--examples", type=Path, default=DEFAULT_EXAMPLES)
    p.add_argument("--models-config", type=Path,
                   help="a models config: the grader --grader names, or its primary, is called with that entry's settings")
    p.add_argument("--grader", help="a grader the models config names, or provider:model, e.g. deepseek:deepseek-flash")
    p.add_argument("--only", help="comma-separated item IDs or patterns, e.g. KM4-*,AO3")
    p.add_argument("--out", type=Path, default=ROOT / "runs/item-tests")
    p.add_argument("--dry-run", action="store_true", help="write the prompts; call nothing")
    p.add_argument("--conversation", type=Path, help="step 1's conversation file; without it, earlier assistant turns are beat placeholders")
    p.add_argument("--grader-reasoning", choices=sorted(GRADER_REASONING),
                   help="the grader's hidden reasoning, over the models config's setting (default off for a "
                        "provider:model it does not list); recorded in the manifest")
    a = p.parse_args(argv)
    try:
        config = models_config.load(a.models_config) if a.models_config else None
        judge = providers.grader_for_run(config, a.grader, a.grader_reasoning)
    except ValueError as e:  # a models config with problems, or a grader it does not name
        p.error(str(e))
    if not a.dry_run and judge is None:
        p.error("--grader (or a models config with a primary grader) is required unless --dry-run")

    try:
        spec = Spec(a.spec, conversation=load_conversation(a.conversation) if a.conversation else None)
    except ValueError as e:  # an edited pinned line
        p.error(str(e))
    raw, examples = ex_mod.load(a.examples)
    bad = ex_mod.problems(spec, examples)
    if bad:
        print("examples file is malformed:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 2
    only = a.only.split(",") if a.only else None
    chosen = [e for e in examples if _selected(e.item, only) and e.status == "active"]
    pending = [e.id for e in examples if _selected(e.item, only) and e.status != "active"]
    if pending:  # said out loud, so a skipped example never reads as covered
        print(f"skipping {len(pending)} example(s) pending an item rewording: {', '.join(pending)}")
    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = a.out / f"{started}-{(judge.target if judge else 'dry-run').replace(':', '_').replace('/', '_')}"
    out.mkdir(parents=True, exist_ok=True)

    if a.dry_run:
        for e in chosen:
            item = spec.items[e.item]
            (out / f"{e.id}.txt").write_text(grader.render_prompt(spec, e.checkpoint, item, e.reply))
        print(f"wrote {len(chosen)} prompts to {out}")
        return 0

    rows, settings = [], providers.grader_settings(judge)
    for i, e in enumerate(chosen, 1):
        rows.append(grade_one(spec, e, judge.target, call, settings))
        r = rows[-1]
        status = "ERROR" if r["error"] else ("ok" if r["correct"] else f"WRONG ({r['verdict']})")
        print(f"[{i}/{len(chosen)}] {e.id}: expected {e.expected}, {status}", flush=True)
    table = summarize(spec, rows)
    manifest = {
        "grader": judge.target, "returned_models": sorted({r["model"] for r in rows if r.get("model")}),
        "sampling": settings, "only": only,
        "grader_name": judge.name, "models_config": providers.config_record(config),
        "prompt": {"version": grader.PROMPT_VERSION, "sha": grader.prompt_hash()},
        "spec": {"id": spec.raw["spec"]["id"], "version": spec.version, "sha": _sha(a.spec)},
        "examples": {"version": raw.get("examples_version"), "sha": _sha(a.examples), "n": len(chosen),
                     "pending_rewording": pending},
        "draft_prefix": any(spec.is_draft_prefix(e.checkpoint) for e in chosen),
        "started": started, "finished": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "usage": {k: sum(r.get("usage", {}).get(k, 0) or 0 for r in rows) for k in ("prompt_tokens", "completion_tokens")},
    }
    if a.conversation:
        manifest["conversation"] = {"path": str(a.conversation), "sha": _sha(a.conversation)}
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "summary.md").write_text(_summary_md(table, manifest))
    usable = sum(t["usable"] for t in table)
    print(f"{usable}/{len(table)} items usable; wrote {out}")
    return 0 if usable == len(table) else 1


if __name__ == "__main__":
    sys.exit(main())
