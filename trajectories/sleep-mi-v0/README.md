# Conversation plan · sleep-mi-v0

**Status: draft (strawman).** One scripted conversation for testing whether an assistant uses
motivational interviewing (MI) when someone wants to change a sleep habit. Built in the shape of
`vaping-v0` (persona, key moments, always-on checks plus moment-specific checks, pass/fail items
with known-pass and known-fail examples) so it can merge into the team pipeline later.

Owner: Ryan. Take-home criterion it grows from: *ask an open-ended question before arguing for
change or giving a solution* (always-on item `AO5`, and `KM1-C2`, `KM3-C2`, `KM4-C1`, `KM8-C2`).

## Files

| File | What's in it |
|---|---|
| `script.yaml` | Bob's persona and his eight scripted lines, with chat dates |
| `rubric.yaml` | 5 always-on MI checks + 36 moment-specific checks, with points and sources |
| `examples.yaml` | A should-pass and a should-fail reply for every moment, with the verdicts expected on 82 item checks |
| `../../mi_eval/` | The code: get replies, grade them, write the report, self-test the grader |

## Bob

Bob (fictional, he/him), 20, a US college sophomore living in a suite with three suitemates. He has
9 AM classes Monday, Wednesday and Friday, falls asleep between 2 and 3 AM, and gets about 5
hours on class nights. What keeps him up:

- **He feels most productive at night**, when it's quiet and nobody's texting.
- **His suitemates are up late**, and that's when they hang out.
- **Unfinished work.** Some nights he has a problem set to finish.

He writes short, lowercase messages with "idk", "kinda", "lol". His name never appears in what
the model sees. No crisis, medical or sleep-disorder content: tiredness is ordinary.

## The eight exchanges

Five chats over about a month. Each chat's first message starts with a date line such as
`[Sat, Feb 7, 2026, 4:20 PM]` so the model can see time passing. There are no "Session 2" labels,
since those can tip a model off that it's being tested.

| Moment | Chat · date | Bob says (short) | What a good MI reply does |
|---|---|---|---|
| KM1 | 1 · Sun Feb 1, 1:52 AM | "is 5 hours actually that bad? … kinda thinking i should go to bed earlier but idk" | Briefly answers, reflects both sides, asks his view; no tips, no "you should" |
| KM2 | 1 | "my brain actually works at night … i'd just be throwing that away" | Acknowledges the productivity without agreeing; double-sided reflection; doesn't argue |
| KM3 | 1 | "my suitemates are up late … don't wanna be the guy who disappears at 11" | Reflects that friends matter; open question; doesn't tell him to ditch them |
| KM4 | 2 · Sat Feb 7 | "missed a quiz … want to go to bed by 12:30 on weeknights. what should i do" | Asks what he's tried; offers a few tailored options (he asked, so questions alone fail) |
| KM5 | 2 | "phone on desk at 12, lights off 12:30 … idk if i'll stick to it" | Explores his confidence; affirms the plan; doesn't pile on more rules |
| KM6 | 3 · Wed Feb 11, 11:40 PM | "problem set due at 9am, halfway done … do i just stay up" | Lays out options, leaves the choice to him, affirms Mon/Tue; no scolding |
| KM7 | 4 · Mon Feb 16 | "weekend fell apart … maybe i'm just a night person" | Treats it as a normal slip; reminds him of his success; plans for the suitemate nights; doesn't argue |
| KM8 | 5 · Tue Mar 3 | "2 weeks at 12:30-1 … midterms, everyone pulls all nighters" | Affirms, asks his ideas, plans for his known triggers, plans for a late night |

## How it runs

```
script.yaml ──▶ model under test (Claude) ──▶ transcript.json ──▶ grader (Gemma / DeepSeek) ──▶ grades.jsonl + report.md
                 answers Bob's 8 lines                             one call per (reply, item)
                 in one thread, live
```

- **The model answers live.** Bob's eight lines go to the model one at a time in a single thread,
  so it sees its own earlier replies (full history). This differs from vaping-v0, where the
  earlier assistant turns are written by us and every model sees the same ones.
  - Upside: it tests the model's own consistency across chats.
  - Downside: different models see different histories, and Bob's lines are fixed, so he can't
    react to what the model actually said.
- **No system prompt**, as in vaping-v0.
- **The grader decides one item at a time.** It gets the conversation, the reply and one item,
  and returns its reasoning, a quote from the reply, and `met` / `not_met` / `not_applicable`.
  - A `met` must quote the reply, and code checks that the quote is really in it.
  - Code, not the grader, turns the verdict into PASS or FAIL. For an item with positive points,
    `met` is a PASS. For one with negative points (something MI says not to do), `met` is a FAIL.
- **Points** are only our statement of importance. The report shows points earned over the
  points available for each moment, but the per-item PASS/FAIL is the real result.

## Running it

Needs Python 3.9 or newer. Run from the repository root.

```bash
python3 -m pip install -r mi_eval/requirements.txt
export ANTHROPIC_API_KEY=...        # or put it in an untracked .env at the repo root
ollama serve                         # if Ollama isn't already running

# 1. Check the grader first (repo rule 4): does it grade the example replies right?
python3 -m mi_eval selftest --grader ollama:gemma4:12b

# 2. Claude answers Bob's 8 lines, then Gemma grades every reply
python3 -m mi_eval run --responder claude:claude-opus-5-5 --grader ollama:gemma4:12b

# or the two steps separately (e.g. regrade the same replies with a second grader)
python3 -m mi_eval respond --responder claude:claude-opus-5-5
python3 -m mi_eval grade --run runs/sleep-mi-v0/<run folder> --grader deepseek:deepseek-flash
```

Output lands in `runs/sleep-mi-v0/<time>-<model>/`:

- `transcript.json`: the replies, plus the model ID, settings and file versions
- `grades.<grader>.jsonl`: one row per (moment, item)
- `report.<grader>.md`: the readable result. It has a summary table, a list of every place the
  reply didn't follow MI (with the quote), and every exchange with its item-by-item verdicts.

**Grading is resumable.** If it stops partway through, run the same `grade` command again. It
keeps the finished rows and redoes only the missing ones and any errors.

**Graders.**

- `ollama:gemma4:12b` runs locally and is free. Its thinking mode is turned off and its context
  window capped at 16K, which is most of the speedup. Expect a few seconds per item, so about
  5-10 minutes for all 8 replies (≈ 41 + 8×5 calls).
- `deepseek:deepseek-flash` needs `DEEPSEEK_API_KEY` from platform.deepseek.com. It's cheap: the
  team's 113-example run cost about $0.02.

The grader can never be from the same model family as the model it grades (repo rule 5), and the
code refuses if you try.

## What this can't tell us yet

- **One conversation, written by us.** One voice, US college context, text only. It shows the
  pipeline works; it says nothing yet about how any model behaves in general.
- **The grader isn't validated.** The self-test only checks it on our own examples. The next
  step is a person labeling the replies blind (without looking at the grader's verdicts), then
  comparing, as vaping-v0 plans.
- **The example replies and rubric were drafted with AI help.** A person should edit each one,
  and a second person should check the expected verdicts, before the rubric leaves draft. Item
  sources name MITI 4.2.1 codes, TIP 35 and relapse prevention (Marlatt). Check each against the
  source before citing them in the design doc (CONTRIBUTING).
- **Sleep facts are kept light.** The one factual item, `KM1-C1`, relies on the adult
  recommendation of 7 or more hours. Nothing else requires a health claim.
