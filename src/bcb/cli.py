"""Command line: `PYTHONPATH=src python -m bcb <command> --config configs/run.<spec>.yaml`.

Exit codes: 0 done; 1 the step finished but recorded problems (check errors, reply or grade
errors); 2 the step refused to run (bad spec, config or data), with the reason code printed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from .errors import BcbError, DataError


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except BcbError as e:
        print(f"error reason_code={e.reason_code}\n{e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"error reason_code=file_not_found\n{e.filename}: file not found", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bcb", description="BehaviorChangeBench pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    def cmd(name: str, func, help_: str, config: bool = True) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, description=help_)
        if config:
            sp.add_argument("--config", required=True, type=Path, help="run config, e.g. configs/run.vaping-v0.yaml")
        sp.set_defaults(func=func)
        return sp

    sp = cmd("spec-check", _spec_check, "Validate a conversation spec", config=False)
    sp.add_argument("--spec", required=True, type=Path)

    sp = cmd("draft", _draft, "Step 1: draft the conversation with the generator model")
    sp.add_argument("--out", type=Path, help="default: conversation.draft.yaml next to the config's conversation")

    sp = cmd("check", _check, "Step 1: check a conversation file against every scriptable constraint")
    sp.add_argument("--conversation", type=Path, help="default: the config's conversation")

    sp = cmd("freeze", _freeze, "Step 1: freeze the edited conversation (the config's conversation)")
    sp.add_argument("--draft", required=True, type=Path)
    sp.add_argument("--version", default="1.0")
    sp.add_argument("--realism-reviewer", required=True, help="a teammate who wrote none of the turns")

    sp = cmd("render", _render, "Print exactly what a model sees at one checkpoint")
    sp.add_argument("--checkpoint", required=True)

    sp = cmd("examples-skeleton", _skeleton, "Write the known-pass/known-fail slots for every item")
    sp.add_argument("--out", type=Path, help="default: the config's examples path; never overwrites")

    sp = cmd("replies", _replies, "Step 2: collect replies from every model at every checkpoint")
    sp.add_argument("--workers", type=int, default=4)

    cmd("packets", _packets, "Build blind labeling packets from the replies")

    sp = cmd("itemtest", _itemtest, "Grade every item's known-pass and known-fail examples")
    sp.add_argument("--grader", required=True)

    sp = cmd("grade", _grade, "Step 3: grade the replies")
    sp.add_argument("--grader", required=True)
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--allow-untested-items", action="store_true",
                    help="grade items whose item tests have not passed; every row still records its test status")

    sp = cmd("tabulate", _tabulate, "Step 4: tables, disagreements and the run manifest")
    sp.add_argument("--labels", type=Path, help="default: runs/<run_id>/labels")
    return p


def _load(args):
    from .config import load_config
    from .spec import load_spec

    cfg = load_config(args.config)
    return cfg, load_spec(cfg.spec_path)


def _load_all(args, conversation: Path | None = None):
    from .conversation import load_conversation

    cfg, spec = _load(args)
    return cfg, spec, load_conversation(conversation or cfg.conversation_path)


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def _spec_check(args) -> int:
    from .spec import load_spec

    spec = load_spec(args.spec)
    per = {cp: len(spec.checkpoints[cp].graded) for cp in spec.checkpoints}
    _print({"spec": spec.id, "version": spec.version, "sha256": spec.sha256, "items": len(spec.items),
            "checkpoints": per, "verdicts_per_model_sample_default": sum(per[c] for c in spec.default_checkpoints)})
    return 0


def _findings_report(findings) -> int:
    from .conversation import MANUAL_CHECKS

    for f in findings:
        print(f)
    errors = sum(1 for f in findings if f.severity == "error")
    print(f"\n{errors} error(s), {len(findings) - errors} warning(s)")
    print("\nStill to check by hand (details.md section 8):")
    for m in MANUAL_CHECKS:
        print(f"  [ ] {m}")
    return 1 if errors else 0


def _draft(args) -> int:
    from .conversation import draft_conversation
    from .providers import make_provider

    cfg, spec = _load(args)
    out = args.out or cfg.conversation_path.with_name("conversation.draft.yaml")
    if out.exists():
        raise DataError(f"{out} exists; drafts are never overwritten. Move it aside to redraft.")
    findings = draft_conversation(spec, make_provider(cfg.generator()), cfg.prompt_path("generator"), out,
                                  out.with_name("generation-record.json"), cfg.generator_family_under_test())
    print(f"wrote {out}")
    return _findings_report(findings)


def _check(args) -> int:
    from .conversation import check_conversation

    _, spec, conv = _load_all(args, args.conversation)
    return _findings_report(check_conversation(spec, conv))


def _freeze(args) -> int:
    from .conversation import freeze_conversation

    cfg, spec = _load(args)
    diff, findings = freeze_conversation(spec, args.draft, cfg.conversation_path, args.version, args.realism_reviewer)
    print(f"froze {cfg.conversation_path} as {args.version}; edits in {diff}")
    for f in findings:
        print(f)
    return 0


def _render(args) -> int:
    from .checkpoints import render_checkpoint

    _, spec, conv = _load_all(args)
    if args.checkpoint not in spec.checkpoints:
        raise DataError(f"unknown checkpoint {args.checkpoint}")
    r = render_checkpoint(spec, conv, args.checkpoint)
    _print({"checkpoint": args.checkpoint, "input_sha256": r.sha256, "turns": list(r.turn_ids), "messages": list(r.messages)})
    return 0


def _skeleton(args) -> int:
    from .itemtests import examples_skeleton
    from .util import write_text_atomic

    cfg, spec = _load(args)
    out = args.out or cfg.examples_path
    if out.exists():
        raise DataError(f"{out} exists; the skeleton never overwrites written examples")
    write_text_atomic(out, examples_skeleton(spec))
    print(f"wrote {out}")
    return 0


def _replies(args) -> int:
    from .providers import make_provider
    from .replies import run_replies

    cfg, spec, conv = _load_all(args)
    if conv.meta.get("status") != "frozen":
        logging.getLogger("bcb").warning("conversation_not_frozen path=%s status=%s", conv.path, conv.meta.get("status"))
    providers = [make_provider(m) for m in cfg.models()]
    summary = run_replies(spec, conv, cfg.run_id, cfg.checkpoints(spec), cfg.samples, cfg.system_prompt, providers,
                          cfg.run_dir / "replies.jsonl", args.workers)
    _print(summary)
    return 1 if any(k != "ok" for k in summary["by_status"]) else 0


def _packets(args) -> int:
    from .packets import build_packets

    cfg, spec, conv = _load_all(args)
    _print(build_packets(spec, conv, cfg.run_id, cfg.run_dir / "replies.jsonl", cfg.run_dir, cfg.packet_seed,
                         cfg.selected_items(spec)))
    return 0


def _grader(cfg, name):
    from .grading import load_grader_prompt
    from .providers import make_provider

    cfg.models()  # enforces README rule 5 against the families under test
    return make_provider(cfg.grader(name)), load_grader_prompt(cfg.prompt_path("grader"))


def _itemtest(args) -> int:
    from .itemtests import run_itemtests

    cfg, spec, conv = _load_all(args)
    provider, prompt = _grader(cfg, args.grader)
    summary = run_itemtests(spec, conv, cfg.examples_path, provider, prompt,
                            cfg.run_dir / f"itemtests.{args.grader}.json")
    _print(summary)
    return 1 if summary.get("failed") else 0


def _grade(args) -> int:
    from .grading import run_grading
    from .itemtests import load_itemtest_status

    cfg, spec, conv = _load_all(args)
    provider, prompt = _grader(cfg, args.grader)
    tests_path = cfg.run_dir / f"itemtests.{args.grader}.json"
    tests = load_itemtest_status(tests_path, provider, prompt) if tests_path.exists() else None
    if tests is None and not args.allow_untested_items:
        raise DataError(f"{tests_path}: no item tests for this grader. Run `bcb itemtest`, or pass "
                        "--allow-untested-items for a dry run (README rule 4).")
    summary = run_grading(spec, conv, args.grader, provider, prompt, cfg.run_dir / "replies.jsonl",
                          cfg.run_dir / f"grades.{args.grader}.jsonl", cfg.selected_items(spec), tests,
                          args.allow_untested_items, args.workers)
    _print(summary)
    return 1 if summary["skipped_items"] or any(k != "ok" for k in summary["by_status"]) else 0


def _tabulate(args) -> int:
    from .tabulate import tabulate

    cfg, spec, conv = _load_all(args)
    summary = tabulate(spec, conv, cfg, args.labels or cfg.run_dir / "labels")
    _print(summary)
    if summary["contains_mock_output"]:
        print("NOTE: this run contains mock output; it is a pipeline check, not a result.", file=sys.stderr)
    return 0
