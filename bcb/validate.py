"""Check data files before a run: conversation, rubric, models config and run file (infra audit 4.2).

    python -m bcb.validate scenarios/activity/walking-v0 rubrics/core-mi.yaml configs/models.yaml
    python -m bcb.validate configs/walking-run.yaml       # a run file: also every file it names, against each other

A file is recognized by its top keys (scenario; rubric; run or scenarios; models or graders); a folder means its
conversation.yaml. Every problem is printed, one line each, and the exit code is 1 if any file has one. A file
without problems may still have warnings, printed under it: what it lacks before it is published (the canary in a
conversation or rubric file, a scenario's license). Warnings never change the exit code. Nothing is called and
nothing is written.
"""

import argparse
import sys
from pathlib import Path

from . import models_config, rubric, runfile, scenario, schema

# in this order: a run file also has a models key, and one without its run block still lists scenarios
KINDS = {"scenario": "conversation", "rubric": "rubric", "run": "run file", "scenarios": "run file",
         "models": "models config", "graders": "models config"}
MODULES = {"conversation": scenario, "rubric": rubric, "models config": models_config}


def kind_of(raw):
    """What kind of file its YAML is (conversation, rubric, run file, models config), or None."""
    return next((KINDS[k] for k in KINDS if isinstance(raw, dict) and k in raw), None)


def check(path, root=runfile.ROOT):
    """(what kind of file it is, its problems, its warnings); a run file's problems and warnings include those of
    every file it names. `root` is the repository's top folder, where a run file's paths start. Warnings are given
    only for a file without problems."""
    path = Path(path)
    if path.is_dir():
        if not (path / "conversation.yaml").is_file():
            return "folder", ["a scenario folder must hold its conversation.yaml, and this one does not"], []
        path = path / "conversation.yaml"
    raw, bad = schema.read(path)
    if bad:
        return "file", bad, []
    if raw is None:
        return "file", ["the file is empty"], []
    kind = kind_of(raw)
    if kind is None:
        return "file", ["cannot tell what kind of file this is: a conversation starts with a scenario block, a rubric "
                        "with rubric, a run file with run, and a models config with models"], []
    if kind == "run file":
        bad = runfile.run_problems(raw, root)
        return kind, bad, [] if bad else _run_warnings(runfile.load(path, root), root)
    module = MODULES[kind]
    bad = module.problems(raw) + (scenario.placement(raw, path, root) if module is scenario else [])
    return kind, bad, [] if bad or module is models_config else module.warnings(module.build(raw, path))


def _run_warnings(run, root):
    """The warnings of every conversation and rubric file a run names, each after the file's path."""
    out = []
    for module, files in ((scenario, run.scenarios), (rubric, run.rubrics)):
        for f in files:
            shown = f.path.relative_to(root) if f.path.is_relative_to(root) else f.path
            out += [f"{shown}: {w}" for w in module.warnings(f)]
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("files", nargs="+", type=Path, help="conversation, rubric, models or run files, or scenario folders")
    p.add_argument("--root", type=Path, default=runfile.ROOT,
                   help="the repository's top folder, where a run file's paths start and where a private scenario "
                        "must sit in a folder named private (default: this repository)")
    a = p.parse_args(argv)
    failed = 0
    for f in a.files:
        kind, problems, warnings = check(f, a.root)
        if problems:
            failed += 1
            print(f"{f} ({kind}): {len(problems)} problem{'' if len(problems) == 1 else 's'}")
            print("".join(f"  - {line}\n" for line in problems), end="")
        else:
            print(f"{f} ({kind}): ok")
            print("".join(f"  - warning: {line}\n" for line in warnings), end="")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
