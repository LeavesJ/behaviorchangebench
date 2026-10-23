# BCB eval infrastructure audit: any conversation, any rubric set, any model

> **Where this sits.** This is the infrastructure audit written on Oct 16 against `ed83abb`. Code comments, messages and docs on `infra/general-formats` cite it as "infra audit N.M" (section N, item or subsection M) and "audit task N" (section 4's task table). What that branch implemented, and what is still open, is in `docs/infra-general-formats.md`. The text below is as audited; nothing has been updated for the branch.

**How this was compiled.** Written 2026-10-16 against the PI's Oct 16 request that the eval infrastructure support any multi-turn conversation, any set of rubrics and any models, using best practices for deploying an eval in the real world. I read every module in `bcb/` and `tests/`, the team `Makefile` and `README.md`, the `trajectories/vaping-v0/` YAML files (the 2,075-line `item-examples.yaml` skimmed), `docs/architecture-proposal.md`, `docs/decisions.md`, and two earlier research notes, on Harbor and on eval best practice. Nothing in the repository was changed: the offline tests were run on a copy in a scratch folder (215 tests, all pass), and three probes were run there too: a minimal physical-activity spec pushed through the core render, grade and score code; the same spec through preflight; and a re-aggregation of the saved Oct 8 verdicts. Outside sources were opened today (Inspect AI docs and source, OpenAI simple-evals and its HealthBench code and one data record, the HealthBench paper, HELM docs and README, lm-evaluation-harness docs, Harbor docs and changelog, PyPI, BIG-bench README) plus Anthropic's wellbeing-eval guidance PDF. File references are `path:line` inside this repository unless they say otherwise. The code audited is branch `research/main-line-run` at commit `ed83abb` (pushed); `origin/main` holds only the kickoff scaffold and the vaping spec, with no `bcb/` code. A second, adversarial pass on the same day re-opened every cited `file:line`, grepped the code for literals the first pass missed, re-ran the tests and probes on a fresh scratch copy, and re-opened the primary pages behind the best-practice claims. Its corrections are made in place and listed at the end under "Verification log".

**Tags.** `[O]` opened and read today. `[O-d]` read today in an earlier research note that opened the primary page on 2026-09-18; the primary page was not re-opened. `[S]` search snippet only. `[C]` my own calculation or probe, reproducible from the files named. `[unverified]` could not be confirmed. Untagged sentences are my assessment.

## 0. The short version

1. **The core is already general; the shell around it is not.** `Spec` rendering, the one-item-per-call grader with its strict parse (strict except that it accepts N/A on any item, 2.1), and the points score have no vaping, Dani or KM logic in them. A made-up physical-activity spec with one session and one check rendered, graded and scored without a code change `[C]`. What is hard-wired is everything that drives that core: the runner, the labeling, comparison and review tools, the preflight and the prefix checks.
2. **Top blockers, in order:** (a) the runner has no `--spec` flag and always loads vaping-v0 (`bcb/pilot.py:241`, via `bcb/item_tests.py:26`); (b) a run can hold only one conversation, because a reply's key is (checkpoint, model, sample) with no scenario ID (`bcb/pilot.py:210`, `:278`); (c) the rubric lives inside the conversation spec (`trajectories/vaping-v0/spec.yaml:162-370`), so experts cannot author or reuse a rubric set on its own; (d) "always-on" means "ID starts with AO" (`bcb/pilot.py:123`, `:142`); (e) labels, compare and review hard-code run folders, labelers, checkpoint lists, the persona's name and the count 117 (`bcb/labels.py:24-50`, `:452`; `bcb/review.py:24-30`); (f) models and grader are a Makefile line with one set of settings for all of them (`Makefile:20-24`, `bcb/pilot.py:49`, `:91`).
3. **Three adopted rules are not in code:** the gate cap (`docs/decisions.md:22-24`; no code reads `rubrics.safety_and_overrefusal.cap`), severity reporting (`Item` has no severity field, `bcb/spec.py:20-29`), and the judge-family check between grader and models under test (enforced only for the generator, `bcb/generate.py:333-347`). No model tripped the KM7 gate on Oct 8, so the missing cap does not change those rankings `[C]`. Further spec fields that no scoring code reads: the per-checkpoint `always_on` map, the per-moment `approach` and `profile.severity`, the harmful-compliance vs overrefusal split and the R-CRISIS scan (2.3). The first of these touches the Oct 8 numbers: the spec fixes AO4 as N/A at KM1, KM2, KM5 and KM6, but the runner grades and scores it there, and the grader's 4 `met` verdicts on those 36 rows count. Honoring the spec gives Gemini 0.6693 (was 0.6764), GPT-6 Astra 0.6696 (unchanged) and Grok 0.5041 (was 0.5184), so the top two swap places by 0.0003, far inside either interval `[C]`.
4. **Deployment basics missing:** no response cache, retries only on HTTP 429, no budget cap or cost estimate, no git commit or package versions in manifests, no item version on verdicts, no canary string, no license file, no private split, and `runs/` is ignored only through this machine's `.git/info/exclude`. Grader dollars are recorded per verdict when the grader runs through OpenRouter (`usage.cost`; the Oct 8 verdicts sum to $18.41 `[C]`), but no manifest or summary adds them up.
5. **Recommendation:** keep BCB's own file formats and grading contract, and run them on Inspect AI as the execution engine (providers, caching, retries, resume, logs, rescoring, clustered errors), after a time-boxed spike that must pass byte-level parity with vaping-v0. Keep Harbor as a later export target, not the engine. If the spike fails, the same formats run on the in-house runner with a cache and wider retries added (tasks 9 and 10).
6. **Reproducing vaping-v0** means four parity checks, not a fresh draw: identical model inputs, identical grader prompts, identical scores from the saved 972 verdicts (Gemini 0.68, GPT-6 Astra 0.67, Grok 0.52; reviewed 0.76, 0.74, 0.59), and identical compare outputs from the committed labels. All four fixtures already exist in the repo, and the adversarial pass re-ran `bcb.compare` and `bcb.review_import` on them: all seven output files came back byte-identical to the committed ones `[C]`. One decision has to come first: whether the new loader honors the spec's fixed N/A for AO4 (item 3). If it does, the score fixture changes on purpose.
7. **The pipeline is not on the default branch.** `origin/main` has the kickoff scaffold and spec only; everything audited here is on `research/main-line-run`. A teammate or the PI who opens the repo's default branch sees none of it.

## 1. Pipeline map, as built

| Stage | Entry point | Reads | Writes (format) | Code |
|---|---|---|---|---|
| Preflight the spec | `make preflight` = `python -m bcb.checks --preflight` | `spec.yaml` | Markdown report to stdout (or `--out`); exit 1 on any error | `Makefile:44-45`; `bcb/checks.py:461-483`; `bcb/preflight.py:114-129` |
| Generate the conversation | `make generate GENERATOR=...` | `spec.yaml`, optional earlier draft | `runs/generate/<UTC>-<generator>/conversation.v1.0-draft.yaml`, `generation-record.json`, `checks.md`, `prompts/<session>.txt` | `Makefile:50-52`; `bcb/generate.py:382-456`, writer `:350-379` |
| Freeze | by hand: edit, copy to `trajectories/vaping-v0/conversation.v1.0.yaml`, save diff, rerun checks | draft | frozen YAML `{meta, turns:[{id, role, text, source, facts, label, client_language, key_moment}]}`; hash into `spec.yaml:13` | `bcb/generate.py:21-27`; `bcb/checks.py:468-477` |
| Item tests | `make item-tests` | `spec.yaml`, `item-examples.yaml` | `runs/item-tests/<UTC>-<grader>/results.jsonl`, `manifest.json`, `summary.md` | `bcb/item_tests.py:105-170` |
| Draw replies | `make draw` = `bcb.pilot --models ... --no-grade` | spec (fixed), conversation, a branches file | `runs/hard-pilot/<UTC>-models/units.jsonl` (one JSON line per checkpoint x model x sample), `scores.csv`, `summary.md`, `manifest.json` | `Makefile:63-64`; `bcb/pilot.py:81-100`, `:276-352` |
| Dry run, references | `make pilot-dry`, `make pilot-refs` | same | `<checkpoint>.txt` model inputs; call counts; graded reference and near-miss replies | `bcb/pilot.py:267-274`, `:251-253`, `:299` |
| Grade | `make pilot RESUME=<copy> GRADE_ONLY=1 NORMALIZE=1` | units on disk | graded lines appended to `units.jsonl`; a hand-made copy folder with `source.json` and `drawn-manifest.json` (no code writes these) | `bcb/pilot.py:59-73`, `:103-117`; read at `bcb/compare.py:36-37` |
| Score | inside the runner | verdicts | per-reply points score; clipped mean per model; core vs scenario; bootstrap CI resampling checkpoints | `bcb/score.py:14-30`; `bcb/pilot.py:120-134`, `:145-199` |
| Label packets | `python -m bcb.labels [out]` (no make target) | spec, conversation, messy branches, three fixed run folders | `key.json`; per person `.xlsx` and packet `.html` (PDF printed by hand) | `bcb/labels.py:448-483` |
| Label import | `python -m bcb.label_import <locked>` | locked `.xlsx`, `key.json`, `corrections.json` | `labels.jsonl` | `bcb/label_import.py:441-462` |
| Compare | `python -m bcb.compare <graded dirs> --out` | graded `units.jsonl`, `labels.jsonl` | `per_item.csv`, `disagreements.primary.csv`, `people_disagreements.csv`, `summary.md`, `scores.md` | `bcb/compare.py:235-263` |
| Review | `python -m bcb.review --out`; then `bcb.review_import` | three fixed graded folders, labels | `review-key.json`, per person `.xlsx` and `.html`; then `disagreements.reviewed.csv`, `scores.reviewed.md` | `bcb/review.py:273-308`; `bcb/review_import.py:119-161` |
| Smoke test | `python -m bcb.e2e` | spec (fixed) | one JSON verdict to stdout | `bcb/e2e.py:19-49` |

Two things to notice. The stages are joined by hand-copied folders and constants, not by a run configuration: the Oct 8 path went draw, copy folder, write `source.json`, grade with resume, then point labels, compare and review at hard-coded folder names. And the label, import, compare and review steps have no `make` targets (`Makefile:14`). Two more, found in the adversarial pass. The runner's defaults are the hard-question pilot's, not the spec's: with no `ONLY`, it runs only the branch IDs of whatever branches file is attached (`bcb/pilot.py:247`, with `hard-pilot.yaml` attached by default at `:48`). It also draws 2 samples (`:221`), where the spec says 3 (`spec.yaml:424`), and `run.checkpoints` (`spec.yaml:415`) is read by nothing. And the drawn source folders (`runs/main-line/`, `runs/oct8-carry/`, `runs/hard-pilot/`) exist only on this machine: none of their files is tracked. So `bcb.labels`, which reads them (`bcb/labels.py:27-28`), cannot rebuild the Oct 4 packets from a clone. The graded copies under `runs/oct8-graded/` are tracked.

## 2. Generality audit

### 2.1 Already general

- **Rendering.** `Spec.turns` and `Spec.messages` build any sessions-and-turns layout, put each session's date line on its first user turn, and cut at any checkpoint turn (`bcb/spec.py:90-142`). Variants (KM7-T, KM7-P) and branch files reuse the same mechanism (`bcb/branches.py:81-96`).
- **Grading contract.** One item per call, reasoning then quote then verdict, quote checked against the reply, `cannot_determine` only on gates, errors never scored as fails (`bcb/grader.py:73-146`). Nothing domain-specific. *Correction:* "N/A only where the item has a condition" holds in the prompt (`:74`, `:82-83`) but not in the parser. `parse` accepts `not_applicable` with any valid code on any item (`:131-139`), so an always-on check with no condition can silently leave a reply's denominator. A probe on AO1 confirmed this `[C]`. None of the 50 N/A verdicts on Oct 8 fell on such an item `[C]`. The four NA codes are also a fixed tuple in code (`:18`), not rubric data.
- **Scoring.** HealthBench-style points over applicable items, clipped mean per model (`bcb/score.py:14-30`). This is the same arithmetic as simple-evals `calculate_score` and `_compute_clipped_stats` `[O]`, with two differences. First, HealthBench grades every item true or false and has no N/A. BCB also drops N/A, `cannot_determine` and errored items from the denominator, so scores are comparable to HealthBench's only in form. Second, the simple-evals code reports an unseeded 1,000-draw bootstrap standard deviation over examples. BCB reports a seeded percentile interval over checkpoints `[O]`.
- **Items and examples.** Polarity, type, `applies_when`, NA code, points and the item's own tie-break are data (`bcb/spec.py:55-70`); the known-pass and known-fail checker reads the persona's name from the spec (`bcb/examples.py:38-76`).
- **Probe `[C]`.** A 30-line spec for "walk 30 minutes on most days" (one session, one checkpoint, one check, no always-on checks) loaded, rendered, produced a 2,199-character grader prompt, parsed a verdict and scored 1.0. The minimum keys are `spec.version`, `target.target_behavior`, `target.goal_at_checkpoint`, `criterion_defaults`, `always_on_metrics`, `key_moments`, `grading.grader_note_template`, `sessions`. *Adversarial pass:* reproduced with an independent 25-line spec (2,228-character prompt, score 1.0 `[C]`). The keys inside those blocks are required too: `sessions[].when.header` (`bcb/spec.py:101`, `:105`), `key_moments[].checkpoint_turn`, and `criterion_defaults.type`, `.scope.applies_when` and `.default_when_unsure` (`bcb/spec.py:56-69`). Assistant turns render as beat placeholders unless a conversation file supplies their text (`bcb/spec.py:126-133`). The probe covers the core only: the runner, smoke test and labeling tools cannot be pointed at another spec at all (2.2).

### 2.2 Axis 1: any multi-turn conversation

| Hard-coding | Where | Effect |
|---|---|---|
| Runner, smoke test, labels, compare and review load vaping-v0 only | `bcb/pilot.py:241` (no `--spec` among `:216-237`); `bcb/e2e.py:29`; `bcb/labels.py:24-26`, `:80-84`; `bcb/compare.py:241`; `bcb/review.py:274`; `bcb/review_import.py:153` | A second conversation cannot be run or labeled without editing code. `item_tests`, `checks` and `generate` do take `--spec` (`bcb/item_tests.py:107`, `bcb/checks.py:465`, `bcb/generate.py:384`). |
| One conversation per run: unit key has no scenario ID | `bcb/pilot.py:210`, `:278`; `bcb/compare.py:47`; `bcb/label_import.py:83-84` | Two scenarios that both have a checkpoint called KM1 would overwrite each other. |
| Conversation, rubric, generation plan, run plan, grading and labeling plan in one 503-line file | `trajectories/vaping-v0/spec.yaml` (sessions `:84-147`, checks `:162-370`, generation `:389-410`, run `:412-444`, grading `:446-458`, labels `:460-468`) | Experts cannot hand over a conversation without also writing generator beats, drafts and rubric blocks. |
| Preflight requires a quit day | `bcb/preflight.py:55-61` | The physical-activity probe failed preflight with one error, no session names the quit day `[C]`. |
| Preflight and generation assume a `generation` block | `bcb/preflight.py:100`, `bcb/checks.py:254-306`, `bcb/generate.py:217` | The probe without that block crashed with `KeyError: 'generation'` `[C]`; a real conversation has no generation plan. |
| Assistant turns must be beats, not text | `bcb/preflight.py:86-87`; text arrives only through a separate conversation file (`bcb/spec.py:126-133`) | A collected real conversation has assistant text, not beats. |
| Vaping and MI keyword heuristics | `HEALTH` `bcb/checks.py:54-56`; `CLINICAL` `:64-65`; `MI_JARGON` `:46-49`; `PREANSWER` keyed KM3, KM4, KM7, KM7-T, KM8 `:70-87`; `COMPARISON` (cigarettes) `:89-90`; KM7 special cases `:383-394` | For any other scenario the pre-answer rules silently produce no flags (`:383` returns an empty pattern list). |
| Branches add exactly one user line right after `through` | `bcb/spec.py:94-97`; `bcb/branches.py:46-56` | No multi-turn continuation (assistant turn plus new user turn) can be expressed. |
| Literal persona name in a check | `bcb/branches.py:57` (`"Dani"`) | Should read `persona.name` as `bcb/examples.py:41` does. |
| ID formats | `F\d\d` `bcb/spec.py:14` (99 facts max); `S\d.[UA]\d`, `H\d`, `KM\d` in `bcb/checks.py:36-38`, `:378`; `bcb/generate.py:99-101`, `:218` | Other ID schemes are mis-flagged or missed. |
| Grader note text | `spec.yaml:449` hard-codes jurisdiction US and age 21 (data, not code) | Must become per-scenario fields. |
| No system prompt | `bcb/spec.py:135-138` never emits one; `spec.yaml:420`; the planned sensitivity arm needs one (`:428-435`) | Cannot run the system-prompt condition. |
| Live mode | `spec.yaml:70-75` | Nothing reads it; fine for now, but no loop exists. |
| *Added:* preflight needs far more than a quit day | `sessions[].when.date` and `.day_offset` (`bcb/preflight.py:38`, `:47`), a US 12-hour header format (`:41`), `reference_facts` (`:75`), `persona.hidden_facts` (`:95`), `generation` (`:100`); the crisis check reads `generation.crisis_screen` (`bcb/checks.py:218`); conversation checks also need `conversation_schema` (`:172`, `:183`) and `persona.style.words_per_turn` (`:317`) | A spec without these crashes with `KeyError` rather than reporting them. Probe order: `date`, then `reference_facts`, `persona`, `generation`, then the quit-day error `[C]`. |
| *Added:* branch files are shaped for the hard-question pilot | every branch needs `reference_reply`, `near_miss_reply` and a check citing a pilot fact `P\d\d` (`bcb/branches.py:18`, `:75-76`), and the spec needs `reference_facts` (`:35`) | An expert-written variant without a reference reply or pilot fact is refused. |
| *Added:* runner defaults are the pilot's | `--only` defaults to the attached branch IDs (`bcb/pilot.py:247`); `--samples` 2 (`:221`) vs `samples_per_checkpoint: 3` (`spec.yaml:424`); the headroom target "about 0.60" is printed in every summary (`bcb/pilot.py:169`) | A bare `make pilot` runs the wrong checkpoints with the wrong sample count. |
| *Added:* persona and dates in the human-facing tools | "Dani" and "21-year-old" in label packets and Sheets (`bcb/labels.py:233`, `:242-253`, `:434`) and review packets (`bcb/review.py:83`, `:109`, `:119`, `:129`, `:220`); "she asks nothing" (`bcb/compare.py:180-181`); the date-word heuristic knows only Jan, Feb and Mar (`bcb/checks.py:62`); the human checklist names KM7-T and KM7-P (`bcb/report.py:31`) | Packets for any other scenario would name the wrong person. |
| *Added:* smoke test | defaults to KM1 and KM1-C3 (`bcb/e2e.py:23-24`); calls the grader at temperature 0 with no reasoning setting (`:40`), unlike a real run's grader settings (`bcb/item_tests.py:33-38`) | It does not exercise the production grader call. |

### 2.3 Axis 2: any rubric set

| Hard-coding | Where | Effect |
|---|---|---|
| Rubric inside the conversation; `rubrics:` block unread | checks at `spec.yaml:162-370`; groups at `:372-385`, read by no code; compare groups by `rubric:` tags instead (`bcb/compare.py:51-57`) | Two sources of truth for rubric membership; no reusable rubric file (`README.md:29` still says "to come"). |
| Always-on = ID prefix "AO" | `bcb/pilot.py:123`, `:142`; `bcb/labels.py:420`; `bcb/labels.py:120` (AO3 rule) ; `bcb/compare.py:180-185`; `bcb/label_import.py:97` | Any rubric whose core checks are not named AO1-AO4 gets wrong core and scenario subscores. |
| Always-on applies at every checkpoint, always as fidelity | `bcb/spec.py:60`, `:76-82` | No approach scoping (MI checks keep firing after a switch to CBT) and no always-on safety gate. |
| Gate cap adopted, not implemented | `docs/decisions.md:22-24`; `spec.yaml:375`; no reader in `bcb/` | Rankings ignore gates. Oct 8: KM7-G1 was `not_met` in all 27 KM7, KM7-T and KM7-P replies `[C]`, so no effect there. |
| One gate hard-coded in reporting | `bcb/compare.py:186-190` (KM7-G1); `graded_at` (`spec.yaml:311`) unread | A new gate gets no table. |
| Severity not loaded | `Item` fields `bcb/spec.py:20-29`; severities exist as data (`spec.yaml:228`, `:311`) | No by-severity results, which the Anthropic guidance asks for `[O]`. |
| No item version on verdicts | verdict row `bcb/pilot.py:71-72`; spec promises `item_id@version` (`spec.yaml:443`); only labels carry versions (`bcb/labels.py:107-116`) | A reworded check mixes with old verdicts unless someone compares spec hashes. |
| Prompt hash partial | `bcb/grader.py:93-94` hashes `TEMPLATE` only; `_POLARITY_WORDS` (`:57-58`) feeds the prompt | A wording change there would not change the recorded hash. |
| N/A choices in Sheets name the persona | `bcb/labels.py:48-49` | Two NA codes only, worded for Dani. |
| N/A offered by matching English phrases | `bcb/grader.py:74` | `applies_when` other than "always" or "always at this checkpoint" turns N/A on. Works, but brittle for expert-written text. |
| *Added:* N/A accepted where not offered | `bcb/grader.py:131-139` | See 2.1. Needs a parser check that mirrors `:74`. |
| *Added:* prompt hash misses more text | `na_line` and `cd_line` wording (`bcb/grader.py:82-85`) also feeds the prompt and is not hashed | Same as the row above it. |
| *Added:* core-check wording is persona- and domain-specific | AO1-AO4 say "she" and "her" throughout, and their examples are about vaping and quitting (`spec.yaml:163-166`) | The "always-on" rubric cannot be reused for another persona or domain without rewording, new versions and new item tests. |
| *Added:* per-checkpoint `always_on` map unread by grading and scoring | `spec.yaml:181`, `:201`, `:265`, `:286` fix AO4 as N/A; `Spec.items_at` ignores the map (`bcb/spec.py:75-82`); only labeling reads it (`bcb/labels.py:103`) | `spec.yaml:453` says such rows are graded anyway to measure N/A agreement. The score then counts any non-N/A verdict: 4 of 36 on Oct 8, which moves Gemini and Grok (section 0, item 3) `[C]`. |
| *Added:* approach and severity already exist as data | session `approach` (`spec.yaml:87`, `:104`, `:119`, `:131`, `:144`); per-moment `approach: {in_prefix, expected, trigger}` (`:176` onward), with compound values such as "safety then lapse_work" (`:302`, `:344`); `profile.severity` and `failure_directions` (`:178` onward); `rubrics.theory_consistency` (`:379-384`). No code reads any of them | The proposal in 4.2 should map these, not invent new fields. A checkpoint's approach can be a sequence, not one value. |
| *Added:* side-by-side failure directions not produced | `spec.yaml:374` lists harmful-compliance and overrefusal items "reported side by side"; compare groups only by `rubric:` tag (`bcb/compare.py:51-57`) | The Anthropic guidance asks for both failure directions `[O]`; no table shows them apart. |
| *Added:* R-CRISIS scan not built | `spec.yaml:386-387` (report-only crisis-resource scan and a flag box in packets); no code mentions it | An adopted report line with no producer. |

### 2.4 Axis 3: any models

| Hard-coding | Where | Effect |
|---|---|---|
| Three providers, all OpenAI-compatible | `bcb/client.py:15-19`; provider-specific body fields `:71-79` | No native Anthropic, Google or xAI path; every other vendor goes through OpenRouter. |
| Models and grader in the Makefile; grader reasoning chosen by substring | `Makefile:20-24`; `Makefile:21` | No model config file; `run.models` in the spec (`spec.yaml:421-422`) is read only by the generator's family check (`bcb/generate.py:124-130`). |
| One setting for all models | `MAX_TOKENS` `bcb/pilot.py:49`; `temperature=None` `:91`; one `--reasoning` `:223` | Cannot give one model a different token limit, reasoning level, seed or system prompt. |
| Grader settings global | `bcb/item_tests.py:28-38` | Same. |
| Judge-family rule only for the generator | `bcb/generate.py:333-347`; pilot only says it in help text (`bcb/pilot.py:219`); labels drop Claude replies by name (`bcb/labels.py:29`) | README rule 5 (`README.md:85`) is not enforced at run time. It already slipped once. The Oct 2 Gemini and Grok draw also drew 30 Claude Opus 5.5 replies, removed by hand before grading (`runs/oct8-graded/main-gemini-grok-20261002T091818Z/source.json`, `drawn-manifest.json`) `[C]`. |
| *Added:* blinding and labeling assume today's three models | the model-name screen for packets lists GPT, Gemini, Grok, Claude and a few others, but not DeepSeek, Kimi, Moonshot, Qwen or Mistral (`bcb/labels.py:45-46`); tabs expect exactly 9 or 3 replies, i.e. 3 models x 3 samples (`:143`, `:170`) | A fourth or open-weight model breaks labeling, and a reply that names itself "DeepSeek" would pass the blinding check. |
| Grader's served model not recorded per verdict | `bcb/pilot.py:71-72`; manifest `returned_models` covers models under test only (`:331`); item tests do record it (`bcb/item_tests.py:67`) | If a router served a different grader, the run would not show it. |
| Retries only on 429 | `bcb/client.py:83-97`; grader retried once (`bcb/pilot.py:64`), model draw not retried (`:90-94`; rerun on resume `:277`) | 5xx and timeouts fail at once. |
| Fixed concurrency | `bcb/pilot.py:227`, `:303`; item tests sequential (`bcb/item_tests.py:146`) | No adaptation to rate limits. |
| Cost | model cost from OpenRouter usage only (`bcb/pilot.py:327`, `bcb/client.py:72`). *Corrected:* grader dollars are not missing. Each verdict keeps OpenRouter's `usage.cost` (`bcb/pilot.py:71-72`, since `client.py:72` asks for it on every OpenRouter call), and the Oct 8 verdicts sum to exactly $18.41 `[C]`. The manifest totals only grader tokens (`:328-329`), and a grader called directly on DeepSeek reports no cost. No cap; pre-run price-sheet estimate promised (`spec.yaml:427`) | Grader spend, the larger part, is known only afterwards and only by summing units by hand. |

### 2.5 Blockers, ranked

1. Runner bound to one spec file and one conversation per run (axis 1, `bcb/pilot.py:210`, `:241`). Nothing multi-domain can run until this changes.
2. Rubric not separable from the conversation (axis 2, `spec.yaml:162-370`). Blocks the experts' parallel rubric authoring.
3. Labels, compare and review hard-coded to the Oct 4 and Oct 8 runs (`bcb/labels.py:24-50`, `bcb/review.py:24-30`, `bcb/compare.py:180-190`). Blocks validating graders on any new scenario.
4. No model config, per-model settings or provider choice (axis 3, `Makefile:20-24`, `bcb/pilot.py:49`, `:91`).
5. "AO" prefix and missing approach scoping (`bcb/pilot.py:123`, `bcb/spec.py:76-82`). Blocks MI to CBT to relapse-prevention scenarios.
6. Gates: no cap, no severity, one hard-coded gate (`bcb/spec.py:20-29`, `bcb/compare.py:186`).
7. Preflight and prefix checks tied to quitting and vaping (`bcb/preflight.py:55-61`, `bcb/checks.py:46-90`).
8. No cache and narrow retries (`bcb/client.py:83-97`). Not a generality blocker, but cost and reliability at scale.
9. Manifest gaps: no git commit, package versions, item versions, grader served model (`bcb/pilot.py:330-345`).
10. Branch shape limited to one replaced user line (`bcb/branches.py:46-56`).

*Adversarial pass, added without re-ranking.* Before any of these: merge the pipeline to the default branch, or say in the README which branch holds it. Beside blocker 5: the core checks' wording is itself vaping- and persona-specific (`spec.yaml:163-166`), so separating the rubric (blocker 2) is not enough to reuse it. Beside blocker 6: decide whether the spec's fixed N/A rows count in the score (2.3), and make the parser refuse N/A where none is offered (2.1).

## 3. Deploying an eval: practice versus BCB today

| Topic | What the sources do | BCB now | Missing |
|---|---|---|---|
| Data and rubric format | HealthBench: one JSONL record per example with `prompt` (message list), `rubrics` (each `criterion`, `points`, `tags`), `example_tags`, `prompt_id`, `ideal_completions_data`, and a `canary` field `[O]` (code and one record). Inspect `Sample`: `input` as a string or chat-message list, `target`, `id`, `metadata`; JSON, CSV and Hugging Face loaders `[O]`. lm-eval: one YAML per task with dataset, splits, templates, metrics and a `metadata.version` `[O]`. HELM: run entries plus a `run_spec.json` per run `[O]`. Harbor: a task folder with `task.toml`, `instruction.md`, `tests/` and RewardKit TOML criteria `[O-d]` | YAML with HealthBench field names (`spec.yaml:3`); items carry `criterion`, `points`, `tags` | A flat per-checkpoint export a third party can load; rubric as its own file |
| Versioning | lm-eval: config version plus code commit make a run shareable `[O]`. Inspect's `EvalSpec` records `task_version`, git `revision` (origin, commit, dirty), `packages`, `model_args`, `model_generate_config`, `model_roles`, dataset sample IDs `[O]` (source). HELM pins a run-entries file and schema per leaderboard version `[O]`. HealthBench data files carry a timestamp in the name `[O]` | spec version and changelog (`spec.yaml:5`, `:16-22`); conversation sha256 (`:13`); grader prompt version and hash (`bcb/grader.py:19`, `:93`); spec, branches, conversation hashes in the manifest, cut to 12 hex (`bcb/pilot.py:55-56`, `:336-340`) | Git commit and dirty flag; package versions; item version on every verdict; full hashes |
| Canary and contamination | HealthBench publishes a canary GUID, puts it in every record, asks readers not to post examples, and keeps a private held-out set `[O]`. BIG-bench puts a canary in every task file and says not to edit it `[O]`. Harbor strips leading Markdown comments from `instruction.md` so a canary never reaches the agent `[O]`. lm-eval offers 13-gram decontamination against a training corpus `[O]`. Anthropic lists contamination under internal validity `[O]` | No canary anywhere in `bcb/`, `trajectories/`, `docs/` (searched) | Mint one BCB GUID; put it in every data file and the README before the repo goes public; a gate check for it |
| Held-out or private split | HealthBench keeps a small private held-out set to detect training on it `[O]`. HELM lists public, gated and private MedHELM variants `[O]`. Judge prompts tuned on a dev split and reported on a separate test split `[O-d]` | Item-selection pilots are kept apart from reported numbers (`bcb/pilot.py:24-25`; `docs/decisions.md:31-39`), which is the right instinct | No private split; expert labels not yet reserved as a test set |
| Caching and resume | Inspect caches by model and base URL, message history, epoch, generate config and tools, one-week default expiry (the response cache is opt-in through the `cache` option, not on by default `[O]`); eval sets resume and reuse finished samples if sample IDs are stable `[O]`. lm-eval keeps a SQLite cache of responses `[O]`. HELM saves every request and response in `scenario_state.json` `[O]`. Harbor reruns only the verifier on recorded outputs (`harbor job regrade`) `[O]` | Resume by (checkpoint, model, sample) (`bcb/pilot.py:202-211`, `:277-291`); draw now, grade later; crash-safe appends (`:310-313`); generator keeps paid output on crash (`bcb/generate.py:449-453`) | A content-addressed response cache, so a regrade or rerun never pays twice |
| Retries and rate limits | Inspect: adaptive concurrency (start 20, max 100), cut by 0.8 on 429 with a 15 s cooldown; unlimited retries with exponential backoff from 3 s by default (capped at 30 minutes per wait); a `--timeout` (the concurrency page calls it a limit on total retry time; the options page calls it a generate timeout, default none); 5xx and timeouts pause scale-up but do not cut the limit `[O]`. The options page still gives `--max-connections` a default of 10, which conflicts with the adaptive default; confirm on the pinned version `[O]`. Eval sets retry failed tasks up to 10 times `[O]`. Harbor does not retry timeouts by default `[O-d]` | 429 only, 4 retries, 20 s doubling or `Retry-After` up to 300 s (`bcb/client.py:21-22`, `:42-48`, `:83-91`); 6 fixed workers | Retry 5xx and timeouts with a cap; adapt concurrency per provider |
| Cost control | Inspect: per-sample `--cost-limit` with a model cost table, `token_limit`, provider prompt caching on Anthropic by default `[O]`. Harbor 0.24 added judge cost estimation `[O]` | Dry run prints call counts (`bcb/pilot.py:272-273`); OpenRouter-reported model cost; Oct 8 grading was 972 verdicts for $18.41 (`docs/reports/2026-10-08-oct8-results.md:22`), about 10 times the $1.83 model spend in the three graded manifests `[C]`. *Corrected:* those manifests count only the replies kept; the drawn manifests total $2.62, because the Gemini and Grok draw also paid for 30 Claude replies that were dropped, so grading cost about 7 times the drawing `[C]` | Pre-run estimate from a pinned price sheet; a hard budget; grader dollars totaled in the manifest (they are already stored per verdict); prompt caching for the grader (each reply's transcript repeats across its ~8 checks: 972 verdicts over 117 replies `[C]`). For any saving, the grader prompt, today one user message, must put the shared note, transcript and reply in a separately cached block ahead of the item text. Inspect's Anthropic prompt caching covers messages up to the last user message `[O]` |
| Seeds and determinism | Inspect `seed` works on some providers only: the options page lists OpenAI, Google, Groq, Mistral, HuggingFace and vLLM, so neither Anthropic nor OpenRouter, BCB's grader path; grader reproducibility advice is temperature 0 plus a seed, and the page warns this reduces but does not remove run-to-run disagreement `[O]`. lm-eval seeds four RNGs by default `[O]`. simple-evals subsamples with a fixed seed `[O]`. Anthropic: several attempts per item, averaged; avoid small prompt changes `[O]` | 3 samples per checkpoint; grader at temperature 0 only when reasoning is off, but Opus needs reasoning on so the vendor temperature applies (`bcb/item_tests.py:33-38`); seeded bootstrap (`bcb/pilot.py:126`) and review shuffle (`bcb/review.py:273`) | Send and record a seed where supported; measure judge flip rate by regrading a subset k times |
| Run manifests and logging | Inspect logs every message, event, score, error and the `EvalSpec` above, with a viewer `[O]`. HELM writes `run_spec.json`, `scenario_state.json`, `per_instance_stats.json` `[O]`. Harbor `result.json` holds agent and model info, cost and exception info `[O-d]`. lm-eval `--log_samples` keeps all inputs and outputs `[O]` | Manifest fields at `bcb/pilot.py:330-345`; units keep reply, verdicts, quotes, reasoning, usage | Raw grader text (only parsed fields kept, `:71-72`); grader served model per verdict; input hash per unit; code-written copy and source records; the main-line manifest names `hard-pilot.yaml` as its branches file because branches always attach (`:242-243`) |
| Provider abstraction | Inspect: `provider/model` names across OpenAI, Anthropic, Google, Grok, DeepSeek, Moonshot, Bedrock, Azure, Together, vLLM, Ollama and others, plus OpenRouter and a `mockllm` test provider; reasoning effort mapped per provider `[O]`. simple-evals ships samplers for OpenAI and Claude `[O]`. Harbor goes through LiteLLM `[O-d]` | Hand-written client for three OpenAI-compatible endpoints | Native vendor paths; per-provider quirks handled in one place, tested |
| Judge versioning and sensitivity | Inspect binds the grader as a model role; `model_graded_qa` takes a list of graders and decides by strict majority, keeping each vote; `inspect score` re-scores a saved log and appends the new scores `[O]`. HealthBench meta-evaluates the grader against physicians (section 8) `[O]`; the Inspect Evals port has a meta-evaluation task and reports trying three judges `[O]`. Anthropic: calibrate against expert labels, use a different model family, read transcripts `[O]` | Prompt versioned and hashed; item tests; second grader by resume (`Makefile:17-19`); human labels, compare, disagreement review | Second-grader comparison table (`spec.yaml:458` promises `disagreements.second.csv`; nothing writes it); self-consistency; the paraphrase and system-prompt arm (`spec.yaml:428-435`); family check at run time |
| Intervals and per-item reporting | HealthBench: bootstrap std of the clipped mean and per-tag scores (simple-evals code); worst-at-k (paper section 6.4; not in the simple-evals `healthbench_eval.py` code) `[O]`. Inspect: `stderr(cluster=...)`, `bootstrap_stderr`, `grouped()`, epoch reducers including `at_least_k`, `pass_at_k` and `pass_k_{k}` (all k attempts succeed, which is BCB's pass^k gate rule as a built-in) `[O]`. Anthropic: break results out by severity; at least 100 distinct items for complex multi-turn evals `[O]` | Bootstrap clustered on checkpoint (`bcb/pilot.py:126-134`); core vs scenario; per-check pass counts (`:182-193`); per-item agreement (`bcb/compare.py:107-132`) | Cluster on scenario once there are several; per-severity and per-approach tables; gate pass^k; paired model differences; 13 checkpoints today against the 100-item guide |
| Licensing and hosting | simple-evals is MIT and, since July 2025, kept only as the reference implementation for HealthBench, BrowseComp and SimpleQA `[O]`; the Anthropic guidance asks for enough open code, data and taxonomy for a third party to reproduce results `[O]`; HealthBench data sit on a public blob store `[O]`. HELM is Apache-2.0 and in maintenance mode since June 1, 2026 `[O]`. Harbor is Apache-2.0 with a Hub (private by default) `[O-d]`. Inspect is MIT `[O]` and Inspect Evals hosts a HealthBench port `[O]` | No `LICENSE` file in the repository; the repo is meant to go public | Pick code and data licenses; decide where data and the private split live |

## 4. Design proposal

### 4.1 Engine: Inspect AI, Harbor, or in-house

| | Inspect AI (0.3.281, 2026-10-15, MIT) `[O]` | Harbor (0.24.0, 2026-10-05, Apache-2.0) `[O]` | In-house (`bcb/`) |
|---|---|---|---|
| Fits a chat model answering a fixed prefix | Yes: `Sample.input` is a message list `[O]` | Built-in agents are harnesses; a plain chat agent must be written `[O-d]`; container per trial | Yes |
| Providers | Native for every vendor in use, plus OpenRouter and `mockllm` `[O]` | LiteLLM `[O-d]` | 3 endpoints |
| Cache, retries, resume | Built in; the response cache is opt-in `[O]` | Regrade built in; missing rewards count as 0 `[O]` | Resume only |
| Logs and manifest | Git revision, packages, configs per run `[O]` | `result.json` per trial `[O-d]` | Partial |
| Rescore with another grader | `inspect score`, appends `[O]` | `harbor job regrade` `[O]` | Copy folder and resume |
| Four-way verdict with quote | Custom scorer; `Score.value` may be a mapping, plus `metadata` and edit history `[O]` (source) | RewardKit criteria are binary, Likert or numeric; N/A `[unverified]` `[O-d]` | Native |
| Stability | Frequent releases (0.3.281 on 2026-10-15); pin | Pre-1.0; 0.24 had breaking RewardKit and verifier changes `[O]` | Ours |
| Cost to adopt | Rewrite runner and a scorer; keep loader, grader prompt, score, labels | Containers, task compiler, custom agent, custom N/A handling | Build cache, retries, manifest, configs ourselves |

**Decision.** Keep BCB's file formats, loader, grader prompt and parse, and scoring as a small library. Run them on Inspect: one `Task` whose samples are (scenario, checkpoint) pairs, epochs as samples per checkpoint, the grader as an Inspect model role, and a scorer that calls `grader.render_prompt` and `grader.parse` per check and returns a mapping of check ID to verdict, with quotes and reasoning in metadata. An exporter writes the existing `units.jsonl` shape (plus scenario ID and item version) so labels, compare and review keep working. Reasons: provider coverage, caching, retries, resume, logging and rescoring are exactly the gaps in section 3, Inspect has them now, and a HealthBench port already runs on it. Harbor stays a later export for live multi-session runs or RL reuse (`spec.yaml:444` already maps checkpoints to tasks); *corrected:* the 0.24 changelog says only that Codex and OpenCode can now be the primary agent in simulated-user evaluations `[O]`. Whether a plain chat model works there was not checked `[unverified]`, so it is no evidence either way. **Hedge:** time-box a two-day spike that must pass the parity tests in 4.4 using `mockllm` and a recorded-verdict replay. If it fails, keep the in-house runner behind the same formats and add tasks 9 and 10.

**Adversarial challenge to the decision.** The recommendation may still be right, but the case is weaker than the table makes it look. (1) What Inspect removes are tasks 9 and 10, sized M and S. Adopting it costs tasks 7 and 8, sized M and L, and a pinned dependency that ships often. (2) The largest generality work is the labeling, compare and review toolchain (task 12, L), plus the vaping wording inside the core checks. Inspect helps with neither. (3) Inspect's `seed` does not reach the Anthropic or OpenRouter grader path, and its response cache is opt-in, so determinism and cost gains for the grader are smaller than "built in" suggests `[O]`. (4) The parity bar is already met by today's code on the saved data (section 0, item 6). So the spike should be judged on what it adds beyond tasks 9 and 10 plus a manifest fix, not on parity alone.

### 4.2 Proposed files (minimal examples)

One conversation per file. Turns hold text; beats, drafts and hidden facts move to an optional `generation.yaml` that only `bcb.generate` reads. IDs are local; the global ID is `<scenario>/<id>`.

```yaml
# scenarios/activity/walking-v0/conversation.yaml
canary: "BCB-CANARY <one GUID, minted once, never edited>"
scenario: {id: walking-v0, version: 0.1.0, domain: physical_activity, language: en-US,
           jurisdiction: US, provenance: authored, split: public}   # authored | synthetic | mixed | real-derived
target_behavior: walk 30 minutes on at least 5 days a week
persona: {display_name: Sam, age: 34}       # packets and checks only; never sent to a model
system_prompt: null
sessions:
  - id: S1
    header: "[Mon, Mar 2, 2026, 7:40 PM]"    # optional; prepended to the session's first user turn
    turns:
      - {id: S1.U1, role: user, text: "how much exercise do adults actually need"}
      - {id: S1.A1, role: assistant, text: "Most guidelines suggest about 150 minutes a week..."}
      - {id: S1.U2, role: user, text: "yeah i know i should move more but i hate the gym"}
  - id: S2
    header: "[Mon, Mar 16, 2026, 8:05 PM]"
    turns:
      - {id: S2.U1, role: user, text: "ok i tried walking at lunch. made it 2 days then work got crazy"}
checkpoints:                                  # each ends on a user turn; the model writes the next turn
  - {id: C1, at: S1.U2, goal: undecided, approach: MI}
  - {id: C2, at: S2.U1, goal: "walking at lunch; stopped after 2 days", approach: RP}
branches:                                     # same history, then one or more new turns ending on a user turn
  - id: C1-KNEE
    prefix_through: S1.A1
    turns: [{id: B1.U1, role: user, text: "my knee hurts when i run, is walking even worth it"}]
    goal: undecided; knee pain
    approach: MI
```

A rubric file holds checks that apply always (filtered by approach) or at named checkpoints. Experts can keep one file per framework and one per scenario.

```yaml
# rubrics/core-mi.yaml
canary: "BCB-CANARY <same GUID>"
rubric: {id: core-mi, version: 0.2.0}
defaults: {default_when_unsure: {desired: not_met, undesired: not_met, gate: cannot_determine},
           na_codes: [NA_NO_ADVICE, NA_NO_REQUEST, NA_NOT_ESTABLISHED, NA_OTHER]}
checks:
  - id: MI.EMPATHY                 # stable, never reused; a wording change bumps version
    version: 1
    applies: always                # every checkpoint whose approach is listed
    approach: [MI, RP]
    group: core                    # replaces the "AO" prefix
    polarity: desired
    points: 5
    criterion: "Shows accurate understanding of what the user feels, wants or worries about..."
    source: [MITI Empathy global]
    tags: [rubric:mi, axis:context_awareness]
  - id: MI.PERMISSION
    version: 1
    applies: {always: true, when: "the reply gives advice or a recommendation", na_code: NA_NO_ADVICE}
    approach: [MI, RP]
    group: core
    polarity: desired
    points: 4
    criterion: "When the reply advises, permission is present..."
# scenarios/activity/walking-v0/checks.yaml (same shape)
#  - id: WALK.C1.OPEN-Q, version: 1, applies: {at: [walking-v0/C1]}, polarity: desired, points: 5, criterion: "..."
#  - id: WALK.G1, version: 1, type: gate, severity: S2, points: null, polarity: undesired,
#    applies: {at: [walking-v0/C1-KNEE], false_positive_at: [walking-v0/C1]}, criterion: "..."
```

```yaml
# configs/models.yaml
models:
  - {id: gpt-6-astra, provider: openrouter, model: openai/gpt-6-astra, family: openai,
     sampling: {temperature: vendor_default, max_tokens: 16000, reasoning: on}}
  - {id: gemini-3.1-pro, provider: openrouter, model: google/gemini-3.1-pro-preview, family: google,
     sampling: {temperature: vendor_default, max_tokens: 16000, reasoning: on}}
graders:
  primary: {provider: openrouter, model: anthropic/claude-opus-5.5, family: anthropic, prompt: grader-v0.3,
            sampling: {temperature: vendor_default, max_tokens: 8000, reasoning: on, json: true}}
  second:  {provider: deepseek, model: deepseek-flash, family: deepseek, prompt: grader-v0.3,
            sampling: {temperature: 0, max_tokens: 1000, reasoning: off, json: true}}
rules: {grader_family_differs_from_every_model: true}
```

```yaml
# configs/oct8-repro.yaml  (the run file)
run: {id: oct8-repro, out: runs/}
scenarios: [scenarios/nicotine/vaping-v0]
checkpoints: ["vaping-v0/KM*", vaping-v0/MS-DECIDE, vaping-v0/MS-WHOLEPLAN, vaping-v0/MS-LAPSE]
rubrics: [rubrics/core-mi.yaml, scenarios/nicotine/vaping-v0/checks.yaml]
models: configs/models.yaml
samples: 3
grade: {with: primary, read: normalized}
gates: {rule: pass_k, k: 3, cap: rank_below}
budget_usd: 25
```

### 4.3 One command and what it does

`python -m bcb run configs/oct8-repro.yaml` (and `make run RUN=...`), with `--dry-run`, `--no-grade`, `--resume <dir>` and `--grader second`. The dry run validates every file against its schema (unknown keys are errors), resolves which checks apply where, refuses a grader in a model's family, prints how many replies and verdicts the run would make and their estimated cost from a pinned price sheet, and stops. A real run writes one folder: the engine log, `units.jsonl` (scenario, checkpoint, model, sample, input hash, reply, served model, and per verdict the check ID and version, grader served model, raw grader text, parsed verdict, quote), `scores.csv`, `summary.md` (score, core and scenario, by approach, by severity, gate pass^k and cap, clustered intervals), and `manifest.json` (git commit and dirty flag, package versions, full hashes of every input file, configs, cost).

Scoping rules: a check applies at a checkpoint if it names that checkpoint, or if it is `always` and the checkpoint's `approach` is in the check's `approach` list (no list means every approach). This is how one scenario can move from MI to CBT to relapse prevention: the checkpoints change `approach`, and the matching core checks switch in and out. A check about whether the switch was licensed is an ordinary targeted check at the switch checkpoint; the whole-conversation version waits for live mode. *Adversarial pass:* vaping-v0 already records these as data, unread by code. Each key moment carries `approach: {in_prefix, expected, trigger}` with licensed trigger codes, and some are sequences ("safety then lapse_work", `spec.yaml:302`, `:344`). Each also carries `profile.severity` and `failure_directions` (`:178` onward), and the approach vocabulary and triggers sit in `rubrics.theory_consistency` (`:379-384`). The converter (task 3) should carry these over, and `approach` should allow an ordered list, not one value. Spec-fixed N/A (the `always_on` map) needs a scoping rule of its own.

### 4.4 Migration that keeps vaping-v0 reproducing

Fresh draws cannot reproduce the Oct 8 numbers, since sampling and the grader are not deterministic. What can be held exactly, and is already in the repo:

| Fixture | Value today `[C]` | Test after migration |
|---|---|---|
| Model inputs, 13 checkpoints (10 main line, 3 carried) | sha256 over the message lists (checkpoints in sorted order, each list as sorted-key JSON) `80d31303f2e9a8b9...`, from `bcb.labels.load_spec()` | New loader gives byte-identical messages per checkpoint |
| Grader prompt | template hash `eededa05c06d` (`grader-v0.3`) | Byte-identical prompt for every (checkpoint, check) pair with a fixed reply; same for every item-test example |
| Scores from saved verdicts | 972 verdicts, 117 replies; every stored reply score recomputed exactly; Gemini 0.6764, Astra 0.6696, Grok 0.5184 (39 replies each), matching `runs/oct8-graded/compare/scores.md` | Re-aggregation from v1 units gives the same tables, intervals (seeded) and main or carried splits |
| Reviewed scores | 0.76, 0.74, 0.59 (`runs/oct8-graded/compare/scores.reviewed.md`) | `rescore` on committed review output gives the same |
| Compare outputs | committed `per_item.csv`, `summary.md`, disagreement CSVs; *verified in the adversarial pass:* re-running `bcb.compare` on the three tracked graded folders and `bcb.review_import` on the tracked locked review Sheets reproduces all seven committed files byte for byte `[C]` | Byte-identical from committed `labels.jsonl` and graded folders |

Steps: (1) commit these as parity fixtures and tests against the current code; (2) write a converter from `spec.yaml` plus `conversation.v1.0.yaml` plus `messy-pilot.yaml` (carried branches) to the new files, mapping AO1-AO4 to `group: core` while keeping their IDs; (3) switch every tool to the new loader, keeping the old `Spec` until parity holds; (4) only then change the engine. A regrade of saved replies with the same grader is a judge-stability measurement, reported as a flip rate, not a parity test. *Adversarial pass:* the score fixture holds only while the new loader keeps today's handling of spec-fixed N/A. If the team decides fixed-N/A rows leave the score (2.3), the expected values become Gemini 0.6693 (from 0.6764), GPT-6 Astra 0.6696 (unchanged) and Grok 0.5041 (from 0.5184) `[C]`, and that change should be its own commit with its own fixture. The label-packet build cannot be a parity test from a clone, because its drawn source folders are not tracked (section 1).

### 4.5 Implementation tasks, in order

| # | Task | Size | Offline test it needs |
|---|---|---|---|
| 1 | Parity fixtures and tests (4.4) | S | Pass on today's code; fail when one pinned line or one point value is changed |
| 2 | Schemas and validator for conversation, rubric, models, run; error messages a behavior scientist can read | M | Minimal valid files pass; each malformed case is named (pattern of `tests/test_pilot.py:35`) |
| 3 | Converter vaping-v0 to new files | M | Byte parity of messages and prompts; same check count and points |
| 4 | Loader with namespaced IDs, `group`, `approach`, `severity`, `version`, scoping rules | M | `items_at` parity on vaping; MI check drops at a CBT checkpoint; two scenarios with a C1 each do not collide |
| 5 | Multi-turn branches (`prefix_through` plus turns) | S | KM7-T, KM7-P and MS-* parity; a two-turn branch renders in order |
| 6 | Model config and provider layer; per-model sampling and system prompt; family rule at run time | M | Fake client receives each model's settings; a same-family grader is refused; manifest records settings |
| 7 | Inspect backend spike, two days (Task, scorer, exporter to units v2) | M | `mockllm` plus recorded-verdict replay reproduces the units and scores of task 1 |
| 8 | `bcb run` on the chosen engine: dry run, cost estimate, budget, resume, manifest v2, units v2 | L | End to end with fake model and grader; resume makes no new calls; budget stops before the first call |
| 9 | Only if the spike fails: response cache keyed on provider, model, messages, settings, sample index | M | Second run makes zero calls; changing temperature misses |
| 10 | Only if the spike fails: retry policy for 429, 5xx and timeouts, with a cap | S | Fake 503 then 200 succeeds; 400 never retried; cap reached is an error, not a fail |
| 11 | Gates: pass^k, cap, per-severity and per-approach tables | M | A synthetic model failing a gate 1 of 3 ranks below; severity counts correct |
| 12 | Labels, compare, review driven by a labeling config (people, runs, plan) and the persona display name; remove AO, KM and 117 literals | L | Existing label tests pass with a vaping config; a toy scenario builds packets naming its own persona |
| 13 | Preflight and prefix checks generalized: anchor-date check optional, pre-answer patterns and keyword lists as data in `generation.yaml` | M | The walking probe passes preflight; vaping findings identical to a stored fixture |
| 14 | Judge checks: second-grader table, k-times self-consistency, paraphrase and system-prompt arm | M | Fake graders with planted disagreements give the expected tables |
| 15 | Canary, license, split | S | Gate fails when a data file lacks the canary; `split: private` paths are ignored by the repo's own `.gitignore` |

## 5. Risks and open questions for the lab's engineer

These are the points to settle with whoever on the UCSF side will own running the pipeline; the Oct 16 meeting page lists the UCSF experts (`docs/meetings/2026-10-16-expert-meeting.md:3`).

1. **Engine choice.** Agree the spike's pass criteria before starting (task 7). Pin the Inspect version; it releases often (0.3.281 on 2026-10-15, per PyPI) `[O]`.
2. **Grader cost at scale.** Oct 8 cost about $0.019 per verdict (972 for $18.41) `[C]`. 100 checkpoints x 4 models x 3 samples x about 9 checks is about 10,800 verdicts, about $205 per grader pass at that rate `[C]`; a second grader, self-consistency and the sensitivity arm multiply it. Prompt caching of the shared transcript could cut this; the saving is `[unverified]`.
3. **Grader determinism.** Opus as served needs reasoning on, so the vendor temperature applies (`Makefile:17-21`); exact regrades are not possible. Report a flip rate instead.
4. **OpenRouter aliases.** Model names can resolve to different versions over time, and Inspect's cache warns about exactly this `[O]`. Use dated IDs where vendors offer them and record the served model on every call, grader included.
5. **Real conversations from friends.** They are personal data. Open: consent wording, whether UCSF needs an IRB determination, de-identification, keeping raw text out of the repo that will go public, and whether pasted assistant text from a consumer app can be redistributed `[unverified]`. These should enter as `provenance: real-derived, split: private` until cleared.
6. **The repo will be public.** `runs/` is ignored only by this machine's `.git/info/exclude`, not `.gitignore`. *Corrected wording:* on another clone new run output is not ignored and can be added by accident. Separately, 150 files under `runs/` are already tracked on the branch, whatever any ignore rule says. They include `labels.jsonl` and `key.json`, which name models, and the per-labeler and per-reviewer Sheets and packets in folders named by first name. The "inside runs/" guards (`bcb/label_import.py:449-450`, `bcb/review.py:299-300`) therefore protect nothing, and the message at `:450` claims git ignores `runs/`. Decide what is published, and whether teammates' names belong in a public repo.
7. **Expert authoring.** Behavior scientists may prefer a spreadsheet to YAML. Decide whether a Sheet-to-YAML importer is in scope and who runs the validator.
8. **ID and version policy.** Who may bump a check version, and what happens to old verdicts and labels (regrade saved replies, as `spec.yaml:457` already says).
9. **Approach semantics.** Is a checkpoint's `approach` the expected approach set by the authors (proposed here), or something the grader must detect in the reply? The first is gradeable now; the second needs its own validated check.
10. **Harbor.** The original brief named Harbor and the deferral was a co-mentor call (`docs/decisions.md:6-9`). Confirm with the PI that an Inspect-based engine with a later Harbor export is acceptable for the paper.
11. *(Removed from this copy: a note about the author's own build setup. The number is kept so that references to later items still match.)*
12. **Default branch.** Merge `research/main-line-run` (or a cleaned successor) into `main` before the repo is shown as the deliverable, or say in the README where the code is.
13. **Fixed N/A in the score.** Decide whether rows the spec fixes as N/A count when the grader disagrees (section 0, item 3). Record the decision in `docs/decisions.md` before the parity fixtures are cut.

## Prompt-injection report

Text addressed to AI readers, found while researching, treated as data and not followed:

- Harbor docs pages (`core-concepts/tasks/instruction.md`, `changelog.md`, `core-concepts/datasets/metrics.md`, `core-concepts/jobs/regrade.md`) carry a header telling automated readers to fetch the documentation index and use it before exploring further. Standard docs-platform boilerplate; not acted on.
- simple-evals `healthbench_eval.py` contains `GRADER_TEMPLATE`, written to an LLM grader by design (product prompt, not an injection). The team's own `bcb/grader.py:27-55` and `bcb/generate.py:49-87` are prompts of the same kind.
- The HealthBench paper asks readers not to post dataset examples online. Addressed to people generally; this digest reproduces no example content.
- No text addressed to an AI agent was found in the team repo outside its prompt templates.
- Adversarial pass: four Harbor pages re-opened (changelog, `tasks/instruction`, `jobs/regrade`, `datasets/metrics`) carry the same header. It tells automated readers to fetch `llms.txt` and use it before exploring further. Not followed. No other page re-opened in this pass (listed below) had text addressed to AI readers. A line on the Inspect providers page about multi-turn agents and prompt caching is documentation about agent workloads, not an instruction.

## Sources opened

- Inspect AI docs `[O]`: caching.html, eval-sets.html, datasets.html, parallelism.html, models-concurrency.html, eval-logs.html, scorers.html, metrics.html, scoring-workflow.html, model-graded.html, tasks.html, options.html, providers.html (first 100,000 characters), solvers.html, at https://inspect.aisi.org.uk/ . Source on GitHub `main` `[O]`: `src/inspect_ai/log/_log.py` (EvalSpec, EvalRevision, EvalDataset fields), `src/inspect_ai/scorer/_metric.py` (Score, Value), `src/inspect_ai/model/_providers/providers.py` (openrouter, mockllm). Inspect Evals HealthBench folder `[O]`: https://github.com/UKGovernmentBEIS/inspect_evals/tree/main/src/inspect_evals/healthbench . PyPI JSON for inspect-ai, harbor, harbor-rewardkit `[O]`.
- OpenAI simple-evals README and `healthbench_eval.py` `[O]`: https://github.com/openai/simple-evals ; first record of `consensus_2025-05-09-20-00-46.jsonl` (field names and canary only) `[O]`. HealthBench paper `[O]`: https://arxiv.org/html/2505.08775v1 (sections 3, 6.4, 8, 12; canary and held-out text checked in the page source).
- HELM `[O]`: README https://github.com/stanford-crfm/helm ; tutorial and reproducing-leaderboards pages at https://crfm-helm.readthedocs.io/en/latest/ . A search snippet first reported the maintenance-mode date `[S]`; the README confirmed it `[O]`. HELM's response cache was not found in the pages opened `[unverified]`.
- lm-evaluation-harness `[O]`: `docs/task_guide.md`, `docs/interface.md`, `docs/decontamination.md` at https://github.com/EleutherAI/lm-evaluation-harness .
- Harbor `[O]`: docs `core-concepts/tasks/instruction.md`, `changelog.md`, `core-concepts/datasets/metrics.md`, `core-concepts/jobs/regrade.md` at https://docs.harborframework.com/ . Everything else on Harbor `[O-d]` from an earlier research note.
- BIG-bench README (canary purpose and placement) `[O]`: https://github.com/google/BIG-bench .
- Anthropic, "What we look for in a wellbeing evaluation" `[O]`, text extracted locally from the PDF. This digest's one direct quote is from its external-validity item: "Don't test on the API if you're measuring risk in a consumer chatbot."
- Earlier research notes `[O]`: on Harbor, and on eval best practice (judge dev and test splits, agreement statistics).
- Re-opened in the adversarial pass, 2026-10-16 `[O]`: Inspect `parallelism.html`, `models-concurrency.html`, `caching.html`, `eval-sets.html`, `model-graded.html`, `scoring-workflow.html`, `scorers.html`, `metrics.html`, `options.html`, `providers.html` (first 100,000 characters); Inspect source `log/_log.py`, `scorer/_metric.py`, `model/_providers/providers.py`; Inspect Evals `healthbench` folder page; PyPI JSON for `inspect-ai` 0.3.281 and `harbor` 0.24.0; Harbor `changelog`, `core-concepts/tasks/instruction`, `core-concepts/jobs/regrade`, `core-concepts/datasets/metrics`; HELM README, `tutorial`, `reproducing_leaderboards`; lm-evaluation-harness `docs/interface.md`, `docs/task_guide.md`, `docs/decontamination.md`; simple-evals README and `healthbench_eval.py`; HealthBench paper HTML (first 100,000 characters: sections 6.4, 8, 12); BIG-bench README; the Anthropic wellbeing-eval PDF. The HealthBench JSONL record was not re-opened, so its field list rests on the first pass.

## Verification log (adversarial pass, 2026-10-16)

**Method.** Every `path` and `path:line` reference in the first pass (about 250 of them) was opened against this repository at `research/main-line-run`, commit `ed83abb`. The code was grepped for literals naming the vaping scenario, the persona, check and checkpoint IDs, branch files, models, providers and graders. The offline tests and the probes were re-run on a fresh copy in a scratch folder, with the repository untouched. The best-practice claims were re-checked on the primary pages listed under "Sources opened". Nothing outside this file was written.

**Confirmed as written.** All 215 offline tests pass `[C]`. The core accepts a non-vaping spec `[C]`. The saved Oct 8 data holds 117 replies and 972 verdicts with 0 errors, and every stored reply score recomputes exactly: Gemini 0.6764, GPT-6 Astra 0.6696, Grok 0.5184 `[C]`. KM7-G1 is `not_met` in all 27 KM7, KM7-T and KM7-P replies `[C]`. The model-input hash `80d31303f2e9a8b9` reproduces, taking checkpoints in sorted order with each message list as sorted-key JSON, concatenated `[C]`. The grader template hash is `eededa05c06d` `[C]`. The graded manifests total $1.83 `[C]`, and $18.41 appears at `docs/reports/2026-10-08-oct8-results.md:22`. Every other `file:line` reference points at code or data that says what the audit says, apart from the corrections below. External claims confirmed on the primary page:
- Inspect: the concurrency numbers, the cache-key components, 10 eval-set retries and stable sample IDs, the strict-majority grader panel with votes kept under `panel`, append-mode rescoring, the `EvalSpec` and `EvalRevision` fields, mapping-valued `Score.value` with edit `history`, clustered `stderr`, `bootstrap_stderr`, `grouped()`, the provider list, `mockllm`, and 0.3.281 under MIT on 2026-10-15.
- Harbor: 0.24.0 under Apache-2.0 on 2026-10-05, judge cost estimation, the RewardKit and verifier breaking changes, `harbor job regrade`, missing rewards counted as 0, and leading comments stripped from `instruction.md`.
- HELM: maintenance mode since June 1, 2026, Apache-2.0, the per-run JSON files, and the public, gated and private MedHELM run entries.
- lm-eval: the SQLite cache, four seeded RNGs, `--log_samples`, `metadata.version` plus commit hash, and 13-gram decontamination against the Pile.
- simple-evals and HealthBench: MIT and the July 2025 notice; the HealthBench canary, private held-out set, "do not reveal examples" request and section 8 meta-evaluation.
- BIG-bench: a canary in every task file, not to be edited.
- The Anthropic PDF: every item cited, including the one quote.

**Corrections made in place (the first pass was wrong or imprecise).**
1. *How compiled:* added the audited branch and commit.
2. *2.1 Grading contract:* the claim that N/A is allowed only where an item has a condition holds in the prompt, not in the parser. `bcb/grader.py:131-139` accepts N/A on any item, as a probe on AO1 showed. NA codes are fixed in code (`:18`).
3. *2.1 Scoring:* "matching simple-evals" is now qualified. BCB also excludes N/A, undetermined and errored items, which HealthBench does not have, and its interval is computed differently.
4. *0 item 4, 2.4 Cost row, 3 Cost row:* "grader tokens counted, not priced" was wrong. OpenRouter's `usage.cost` is stored on every verdict and sums to $18.41, though nothing totals it. A direct DeepSeek grader returns no cost.
5. *3 Cost row:* the $1.83 model spend covers only the replies kept. The draws cost $2.62, including 30 Claude replies that were dropped, so grading cost about 7 times drawing, not 10. "~9 checks" per reply is ~8.3 (972/117).
6. *3 Caching row and 4.1 table:* Inspect's response cache is opt-in.
7. *3 Retries row:* how `--timeout` is described on two pages, the 30-minute backoff cap, the fact that 5xx errors do not cut the limit, and the conflicting `--max-connections` default of 10 on the options page.
8. *3 Seeds row:* Inspect's `seed` is documented for OpenAI, Google, Groq, Mistral, HuggingFace and vLLM only, so it does not reach the current grader path.
9. *3 Intervals row:* worst-at-k is in the HealthBench paper (section 6.4), not in the simple-evals code. Added Inspect's `pass_k_{k}` reducer, which is the pass^k gate rule.
10. *3 Licensing row:* the "two others" simple-evals keeps are named (BrowseComp, SimpleQA). Added the Anthropic call for open code and data.
11. *4.1 Decision:* "its simulated user still targets coding agents" overstated the changelog. Replaced with what it says, and the rest is marked unverified.
12. *5 item 6:* "another clone tracks it" was imprecise. Replaced with the facts: new output is not ignored on another clone; 150 files under `runs/` are already tracked, including labeler-named Sheets; and the message at `bcb/label_import.py:450` claims git ignores `runs/`.
13. *5 preamble:* removed a named person's role, which came from a non-public draft and is not a public professional fact this pass could confirm. Replaced with a pointer to the team meeting page.

**Added (missed by the first pass).**
14. *0 item 7, 5 item 12:* `origin/main` has no pipeline code.
15. *0 item 3, 2.3, 4.4, 5 item 13:* the spec's per-checkpoint `always_on` map, which fixes AO4 as N/A at KM1, KM2, KM5 and KM6, is read only by the labeling code. 4 of 36 grader verdicts there counted in the Oct 8 scores. Honoring the map moves Gemini to 0.6693 and Grok to 0.5041, and the top two swap by 0.0003 `[C]`.
16. *2.3:* the AO1-AO4 wording is persona- and vaping-specific (`spec.yaml:163-166`).
17. *2.3, 4.3:* `approach` (some values are sequences), `profile.severity`, `failure_directions` and `theory_consistency` already exist as data that no code reads.
18. *2.3:* the side-by-side harmful-compliance vs overrefusal report (`spec.yaml:374`) and the R-CRISIS scan (`:386-387`) are not built. The prompt hash also misses `bcb/grader.py:82-85`.
19. *2.2:* preflight's full list of required blocks, with the crash order from a probe `[C]`. The branch-file shape is tied to the hard-question pilot (`bcb/branches.py:18`, `:75-76`).
20. *1, 2.2:* the runner's defaults are the pilot's. `--only` defaults to branch IDs, `--samples` defaults to 2 against the spec's 3, `run.checkpoints` is unread, and the headroom target is hard-coded.
21. *2.2:* the persona's name and age in label and review packets and in compare text; the Jan-Mar-only date heuristic; KM7-T and KM7-P in the human checklist; the smoke test's defaults and grader settings.
22. *2.4:* the blinding regex misses DeepSeek, Kimi, Moonshot, Qwen and Mistral, and the labeling tabs assume 3 models x 3 samples. Evidence that the judge-family rule already slipped: 30 Claude replies were drawn on Oct 2 and removed by hand.
23. *1, 4.4:* the drawn source folders are not tracked, so `bcb.labels` cannot rebuild from a clone. Parity already holds: `bcb.compare` and `bcb.review_import` reproduce all seven committed outputs byte for byte `[C]`.
24. *2.5:* a note on the default branch, the core-check wording and the fixed-N/A decision, with no re-ranking.
25. *4.1:* a challenge to the Inspect recommendation, weighing what it removes (tasks 9-10) against what it costs (tasks 7-8), and noting what it leaves untouched (task 12, the core-check wording).
26. *Prompt-injection report and Sources opened:* entries for this pass.

**Not verified in this pass.** The first-pass probe's exact 2,199-character prompt (it used a different spec; the result was reproduced with another). The HealthBench JSONL field list (the record was not re-opened). The Harbor claims tagged `[O-d]`. The names of the three judges the Inspect Evals HealthBench port tried (only "three" is relied on). HELM's response cache remains `[unverified]`.
