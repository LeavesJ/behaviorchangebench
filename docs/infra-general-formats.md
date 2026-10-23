# General formats: any conversation, any rubric set, any model

This branch (`infra/general-formats`, cut from `research/main-line-run` at `ed83abb`) moves the pipeline toward the
Oct 16 ask: run any multi-turn conversation, under any set of rubrics, against any models. It follows the infra
audit of Oct 16 (`docs/infra-audit.md`, sections 1, 2 and 4) and covers its tasks 1 to 6, 11 and 15. Everything is offline. Nothing in this
change calls a model, and vaping-v0 reproduces exactly: the same model inputs, the same grader prompts and the same
Oct 8 scores.

## What changed

**New data files.** One conversation per file, rubrics in their own files, a models config and a run file. These
are the formats in audit 4.2.

| File | What it holds |
|---|---|
| `conversation.yaml` | One scenario: sessions of turns with their text, the checkpoints where a model answers (each ends on a user turn), and branches that keep the history up to a turn and then add one or more turns of their own. Each checkpoint can give its `goal`, its expected `approach` (one name, a sequence such as `[safety, lapse_work]`, or the spec's full block with trigger), its `failure_directions` and its `always_on` map. |
| rubric files | Checks that apply `always` (narrowed by `approach: [...]` when given) or `at: [<scenario>/<checkpoint>]`. `group: core` replaces the "AO" prefix. Gates have `type: gate`, a `severity` and no points. Each check carries its own `version`. |
| `models.yaml` | The models under test and the graders (`primary`, `second`, ...), each with its own provider, family and sampling (temperature, max_tokens, reasoning, and for graders JSON). A model can carry a `system_prompt`. If a grader shares a family with any model, the file is refused (README rule 5). |
| run file | Which scenarios, checkpoint patterns, rubrics and models config one run uses, the samples, the grader and two undecided scoring rules (below). Paths start at the repository's top folder. |

IDs are local to a file. Other files name a checkpoint `<scenario>/<checkpoint>`. Two scenarios can therefore each
have a `C1`, or a check with the same id, without overwriting each other.

**New modules** (in `bcb/`):

| Module | Does | Audit task |
|---|---|---|
| `schema.py`, `scenario.py`, `rubric.py`, `models_config.py`, `runfile.py` | Read and check each file. Every problem is listed at once, in plain words: where it is, what is wrong, and what to write instead. Unknown or repeated keys are errors. | 2 |
| `validate.py` | `python -m bcb.validate <files or run file>`: checks files alone and against each other. It calls nothing. | 2 |
| `loader.py` | Resolves which checks are graded at each checkpoint (rules below) and gives every ID its scenario. | 4 |
| `render.py` | Builds what the model and the grader see from the new files, by bcb/spec.py's rules, so output is byte for byte the same. | 4, 5 |
| `convert.py` | `python -m bcb.convert` writes vaping-v0 into `trajectories/vaping-v0/v2/`. `--check` says whether that folder is out of date. It refuses to write unless every model input and grader prompt matches today's code. | 3 |
| `providers.py` | Turns a models-config entry into a call with its own settings and system prompt. It enforces the family rule when a run starts, before anything is called. | 6 |
| `gates.py`, `results.py` | `python -m bcb.results <run file> <graded folders>` adds scores up again from saved verdicts and prints rank, gate pass^k, counts by severity and scores by approach. It calls nothing. | 11 |

**Changed modules.** `bcb.pilot`, `bcb.item_tests` and `bcb.generate` take `--models-config`. `--models` and
`--grader` can then name entries from that file, and the manifest records the config's hash and what each model was
sent. Without `--models-config` every call is the same as before. `bcb.client` sends no `max_tokens` when it is
given none, which is what a config's `vendor_default` means. The Makefile gains `MODELS_CONFIG=...`. Without it
every target expands to the same command as before (checked target by target with `make -n`).

**Publishing (task 15).** Every conversation and rubric file carries BCB's canary line (`bcb/schema.py`), and the
gate fails on one in the repository that lacks it. A scenario with `split: private` must sit in a folder named
`private`, which `.gitignore` now keeps out of git at any depth. The data license is not chosen yet. `bcb.validate`
warns about that, and no license file is written.

**Data.**

- `trajectories/vaping-v0/v2/` holds vaping-v0 in the new formats. `bcb.convert` generates it, so don't edit it by
  hand.
- `tests/fixtures/vaping-v0/` holds the parity fixtures.
- `tests/fixtures/walking-smoke/` holds the smoke test's files.

### Which checks are graded where

A check is graded at a checkpoint in three cases:

- it names that checkpoint under `at`;
- it names it under `false_positive_at`, for a gate graded to see whether it fires wrongly;
- it applies `always` and its `approach` list includes the checkpoint's expected approach. A check with no list
  applies under every approach, and a sequence counts under each of its steps.

So when a scenario moves from MI to relapse prevention, the MI-only core checks drop out at the later checkpoints.
Within a checkpoint the order matches the old code: the always-on checks rubric by rubric, then the checkpoint's own,
then reused checks, then false-positive gates.

## Running a new scenario, rubric set or model set

1. **Write the files.** `tests/fixtures/walking-smoke/` is a minimal working set. It has a two-session
   physical-activity conversation with checkpoints C1 (MI) and C2 (relapse prevention), a core rubric with one
   check that always applies and one scoped to MI, a scenario rubric with one check at C2, and a models config with
   two models and a grader. Copy its shape. Put the canary line at the top of each conversation and rubric file.
2. **Check them.** `python -m bcb.validate configs/<run>.yaml` reads the run file and every file it names. It
   reports each problem on one line, and exits 1 if any file has a problem. Warnings, such as a missing license,
   never fail it.
3. **Draw and grade.** For a *new conversation* there is no single command yet. `bcb.pilot` still reads
   `trajectories/vaping-v0/spec.yaml` only (audit task 8). For now the whole path is the library code that
   `tests/test_walking_smoke.py` drives with a fake client:
   - `runfile.load` reads the run file;
   - `loader.Suite` resolves the checks;
   - `pilot.run_unit` draws a reply from a `render.View` with the model's own settings;
   - `pilot.grade` grades each applied check with the grader-v0.3 prompt and strict parse;
   - `pilot.score_unit` scores the reply.

   No code had to change for that scenario to go through. For a *new model set on vaping-v0*, the existing
   commands work today. The runner's other defaults are still the hard-question pilot's (audit 2.2), so name the
   frozen conversation, the checkpoints and the samples:

   ```bash
   # every model in the file, each with its own settings
   make draw MODELS_CONFIG=configs/<models>.yaml CONVERSATION=trajectories/vaping-v0/conversation.v1.0.yaml \
       ONLY='KM*' SAMPLES=3
   # names from the file; GRADER=second picks its second grader
   make pilot MODELS_CONFIG=configs/<models>.yaml PILOT_MODELS=<name>,<name> GRADER=second RESUME=<drawn run folder>
   ```

   A grader in the family of any model being drawn or graded is refused before the first call.
4. **Add up the results.**

   ```bash
   python -m bcb.results configs/<run>.yaml <graded run folders> [--gate-cap rank_below] [--out results.md]
   ```

   It reads `units.jsonl` in each folder. When a run has more than one scenario, each unit must say its `scenario`.
   A verdict on a check the run's rubrics do not grade at that checkpoint stops the command. That way replies
   graded on other rubrics are never mixed in silently.

## Opt-in and undecided

The team has not settled two scoring rules. Both default to what the Oct 8 numbers used. Only `bcb.results` reads
them. The runner's own `summary.md` keeps today's rule for both.

- **Spec-fixed N/A (`static_na` in the run file).** The spec fixes AO4 as not applicable at KM1, KM2, KM5 and KM6,
  but those rows are graded anyway, and 4 of the 36 got a verdict that counts. The default is `count`, as on Oct 8.
  `exclude` leaves the rows out:

  | Model | `count` (Oct 8) | `exclude` |
  |---|---|---|
  | Gemini | 0.6764 | 0.6693 |
  | GPT-6 Astra | 0.6696 | 0.6696 |
  | Grok | 0.5184 | 0.5041 |

  With `exclude`, the top two swap places by 0.0003. To change the default, record the decision in
  `docs/decisions.md` and rebuild the parity fixtures in a commit of their own.
- **The gate cap (`gates.cap` in the run file, or `--gate-cap`).** The cap was adopted on 2026-09-27 but never
  applied to a reported number. The default is `none`: a failed gate is reported and moves no rank. With
  `rank_below`, a model that fails any gate's pass^k ranks below every model that failed none. On Oct 8 the grader
  called KM7-G1 not met in all 27 KM7, KM7-T and KM7-P replies, so turning the cap on moves nothing there.

Also open, with nothing enforced:

- the data license (the PI's call);
- whether real conversations from friends must enter as `split: private` (audit 5.5).

## Not done

| Audit task | What is missing |
|---|---|
| 7 | The Inspect AI spike. Nothing here depends on Inspect, and `requirements.txt` is unchanged. |
| 8 | A `bcb run <run file>` command, which would add: a dry run with a cost estimate and a budget stop, resume, manifest v2, and units v2 carrying the scenario ID and each verdict's check version. Until it exists, `bcb.pilot` is bound to vaping-v0's spec.yaml, and its units have no scenario ID. |
| 9 | A response cache. |
| 10 | Retries for 5xx errors and timeouts. Today only 429 is retried. |
| 12 | Labels, compare and review driven by a config. They still hard-code the Oct 4 and Oct 8 runs, the persona's name, the AO and KM IDs, and 117. |
| 13 | Preflight and prefix checks that work beyond quitting and vaping (quit day, keyword lists, KM patterns). |
| 14 | Judge checks: a second-grader table, k-times self-consistency, and the paraphrase and system-prompt arm. A models-config entry can carry a system prompt, and `bcb.pilot` sends it, but the paraphrase arm and the table that compares the arms are not built. |

Also still open from the audit:

- The grader's parser accepts `not_applicable` on any check, not just one with a condition (2.1).
- The prompt hash covers the template only (2.3).
- AO1 to AO4 are worded for Dani and vaping, so the core rubric cannot yet be reused as is.
- The R-CRISIS scan and the side-by-side harmful-compliance vs overrefusal table are not built.
- The pipeline is still not on the default branch.

## How parity is protected

- **Fixtures from tracked files.** `tests/test_parity.py` checks four things against `tests/fixtures/vaping-v0/`:
  - the messages each model received at the 13 Oct 8 checkpoints (sha256 `80d31303f2e9a8b9...`);
  - the grader prompts: every check at every checkpoint, every saved Oct 8 verdict, and every item-test example
    (template `grader-v0.3`, `eededa05c06d`);
  - every reply score and model score re-added from the 972 saved verdicts (0.6764 / 0.6696 / 0.5184; reviewed
    0.76 / 0.74 / 0.59);
  - the seven compare and review outputs, rewritten byte for byte.

  The fixtures pass on this branch and on the untouched `ed83abb` code. So they pin the old behavior, not the new
  one. A failure names the input that changed. Rebuild them (`python -m tests.parity --write`) only for a change the
  team decided, in a commit of its own.
- **The new formats against the old code.** `tests/test_convert.py` checks three things:
  - `v2/` is what the converter writes from today's sources;
  - read through the new loader, it sends the same messages and grader prompts and grades the same checks in the
    same order with the same points;
  - re-adding the saved verdicts under its rubrics gives the pinned scores.

  `bcb.results` on `v2/oct8-repro.yaml` gives 0.6764 / 0.6696 / 0.5184.
- **The converter refuses** to write anything that would change a model input or a grader prompt.
- **Old tests unchanged.** The 215 tests from before still run without edits. Only additions were made to the three
  test files this change touched.
- **New tests checked against breakage.** Each new test was run against a deliberate break of the code it guards,
  failed, and passed again once the code was restored.
