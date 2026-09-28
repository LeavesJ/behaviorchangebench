"""Item tests (README rule 4): an item is used only after its grader gets the item's own
known-pass and known-fail examples right.

Examples live in trajectories/<id>/examples.yaml, written by a person and checked by a second.
An item is `passed` when it has at least one known-pass and one known-fail example at its current
version and every one is graded as expected; `failed` when any is not; `untested` otherwise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checkpoints import render_checkpoint
from .conversation import Conversation
from .errors import DataError
from .grading import GraderPrompt, grade_one, graded_items_for
from .providers import Provider
from .spec import Spec
from .util import dump_yaml, load_yaml, sha256_file, utc_now, write_text_atomic

EXAMPLES_SCHEMA = "bcb.examples/1"
ITEMTESTS_SCHEMA = "bcb.itemtests/1"
EXPECTS = ("pass", "fail")
_KEYS = {"item", "item_version", "checkpoint", "expect", "reply", "author", "checked_by", "note"}


@dataclass(frozen=True)
class Example:
    index: int
    item: str
    item_version: str
    checkpoint: str
    expect: str
    reply: str | None
    author: str | None
    checked_by: str | None


def load_examples(path: str | Path, spec: Spec) -> list[Example]:
    path = Path(path)
    raw = load_yaml(path)
    if not isinstance(raw, dict) or raw.get("schema") != EXAMPLES_SCHEMA or raw.get("spec_id") != spec.id:
        raise DataError(f"{path}: needs schema {EXAMPLES_SCHEMA} and spec_id {spec.id}")
    out: list[Example] = []
    problems: list[str] = []
    for n, e in enumerate(raw.get("examples") or []):
        where = f"{path}: examples[{n}]"
        if not isinstance(e, dict) or set(e) - _KEYS:
            problems.append(f"{where}: unknown keys {sorted(set(e or {}) - _KEYS)}")
            continue
        item, cp, expect = e.get("item"), e.get("checkpoint"), e.get("expect")
        if item not in spec.items:
            problems.append(f"{where}: unknown item {item!r}")
            continue
        if cp not in spec.checkpoints or item not in {g.item.id for g in spec.checkpoints[cp].graded}:
            problems.append(f"{where}: {item} is not graded at checkpoint {cp!r}")
            continue
        if expect not in EXPECTS:
            problems.append(f"{where}: expect must be one of {EXPECTS}")
            continue
        reply = e.get("reply")
        if reply is not None and (not isinstance(reply, str) or not reply.strip()):
            problems.append(f"{where}: reply must be null (not written yet) or non-empty text")
            continue
        if reply is not None and not (e.get("author") and e.get("checked_by")):
            problems.append(f"{where}: a written example needs `author` and `checked_by`")
            continue
        if reply is not None and e.get("author") == e.get("checked_by"):
            problems.append(f"{where}: `checked_by` must be a second person")
            continue
        out.append(Example(n, item, str(e.get("item_version")), cp, expect, reply, e.get("author"), e.get("checked_by")))
    if problems:
        raise DataError("\n".join(problems))
    return out


def run_itemtests(spec: Spec, conv: Conversation, examples_path: Path, provider: Provider, prompt: GraderPrompt,
                  out_path: Path) -> dict[str, Any]:
    examples = load_examples(examples_path, spec)
    rendered = {}
    items: dict[str, dict[str, Any]] = {
        it.key: {"status": "untested", "n_known_pass": 0, "n_known_fail": 0, "n_unwritten": 0, "n_stale": 0, "cases": []}
        for it in spec.items.values()
    }
    for ex in examples:
        item = spec.items[ex.item]
        entry = items[item.key]
        if ex.reply is None:
            entry["n_unwritten"] += 1
            continue
        if ex.item_version != item.version:
            entry["n_stale"] += 1  # written against an older wording; a person must re-check it
            continue
        if ex.checkpoint not in rendered:
            rendered[ex.checkpoint] = render_checkpoint(spec, conv, ex.checkpoint)
        result = grade_one(spec, provider, prompt, rendered[ex.checkpoint], ex.reply,
                           graded_items_for(spec, ex.checkpoint, ex.item))
        want = ex.expect == "pass"
        entry["n_known_pass" if want else "n_known_fail"] += 1
        entry["cases"].append({
            "index": ex.index, "checkpoint": ex.checkpoint, "expect": ex.expect, "status": result["status"],
            "verdict": result["verdict"], "pass": result["pass"], "ok": result["status"] == "ok" and result["pass"] is want,
            "quote": result["quote"], "reasoning": result["reasoning"], "reason_code": result["reason_code"],
        })
    for entry in items.values():
        if any(not c["ok"] for c in entry["cases"]):
            entry["status"] = "failed"
        elif entry["n_known_pass"] and entry["n_known_fail"]:
            entry["status"] = "passed"
    doc = {
        "schema": ITEMTESTS_SCHEMA, "created_at": utc_now(), "grader": provider.config.describe(),
        "grader_prompt_sha256": prompt.sha256, "examples": {"path": str(examples_path), "sha256": sha256_file(examples_path)},
        "spec": {"id": spec.id, "version": spec.version}, "items": items,
    }
    write_text_atomic(out_path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    summary: dict[str, int] = {}
    for entry in items.values():
        summary[entry["status"]] = summary.get(entry["status"], 0) + 1
    return summary


def load_itemtest_status(path: Path, provider: Provider, prompt: GraderPrompt) -> dict[str, str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != ITEMTESTS_SCHEMA:
        raise DataError(f"{path}: not an item-test result ({ITEMTESTS_SCHEMA})")
    if doc["grader"]["model_id"] != provider.config.model_id or doc["grader_prompt_sha256"] != prompt.sha256:
        raise DataError(f"{path}: item tests were run with {doc['grader']['model_id']} and prompt "
                        f"{doc['grader_prompt_sha256'][:12]}; rerun `bcb itemtest` for this grader and prompt")
    return {key: entry["status"] for key, entry in doc["items"].items()}


def examples_skeleton(spec: Spec) -> str:
    """One known-pass and one known-fail slot per item, at the item's home checkpoint. Always-on
    items get slots at the first default checkpoint where the spec says they apply."""
    rows: list[dict[str, Any]] = []
    for item in spec.items.values():
        if item.home is not None:
            cp = item.home
        else:
            cp = next((c for c in spec.default_checkpoints
                       for g in spec.checkpoints[c].graded if g.item.id == item.id and g.expected == "applies"),
                      spec.default_checkpoints[0])
        for expect in EXPECTS:
            rows.append({"item": item.id, "item_version": item.version, "checkpoint": cp, "expect": expect,
                         "reply": None, "author": None, "checked_by": None})
    header = (
        "# Known-pass and known-fail replies, one pair per item at minimum (README rule 4; details.md section 8).\n"
        "# Written by a person, checked by a second person against reference_facts. `reply: null` means not written.\n"
        "# A reply is the assistant's next turn at `checkpoint`. `expect: pass` means the item should pass on it:\n"
        "# for a desired item the behavior is present, for an undesired item it is absent.\n"
        "# Add rows freely (more checkpoints, borderline cases). Bump item_version only with the item.\n"
    )
    return header + dump_yaml({"schema": EXAMPLES_SCHEMA, "spec_id": spec.id, "examples": rows})
