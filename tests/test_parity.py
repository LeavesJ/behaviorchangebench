"""Parity with today's vaping-v0: model inputs, grader prompts, Oct 8 scores and compare outputs (infra audit 4.4).

The fixtures in tests/fixtures/vaping-v0 were cut from tracked files by tests/parity.py. A failure here means a
change reached what a model sees, what the grader reads, or a number already reported. If the team decided on that
change, rebuild the fixtures and commit them on their own, saying why; otherwise it is a regression.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from bcb import grader, labels as lb
from bcb.score import reply_score
from tests import parity

REBUILD = (" If the team decided on this change, rebuild with `python -m tests.parity --write` and commit the "
           "fixtures on their own, saying why.")
UNDECIDED = (" Whether these rows count in the score is undecided: the spec grades them anyway so N/A agreement is "
             "measured (spec.yaml grading.static_na), and docs/decisions.md says nothing about the score. Keep today's "
             "rule (they count), or record the decision in docs/decisions.md and rebuild the fixtures in a commit of "
             "its own.")


def fixture(name):
    return json.loads((parity.FIXTURES / name).read_text())


def what_changed(pinned):
    """Which inputs differ from the ones the fixture was cut from, so a failure says where to look."""
    now = parity.provenance()
    names = [n for n, h in pinned["about"]["inputs"].items() if now.get(n) != h]
    return (f" Changed since the fixture was cut: {'; '.join(names)}." if names
            else " None of the spec, conversation, branch, example or saved run files changed since the fixture was "
                 "cut, so the code did.")


def first_difference(pinned, now):
    """The first line where two lists of fixture lines part, in words; None when they are the same."""
    for n, (a, b) in enumerate(zip(pinned, now), 1):
        if a != b:
            return f"line {n} was {a!r}, now {b!r}"
    if len(pinned) != len(now):
        return f"{len(pinned)} lines pinned, now {len(now)}"
    return None


def first_changed_message(pinned, now):
    """Where a checkpoint's message list first differs, as message number, turn and role."""
    for n, (a, b) in enumerate(zip(pinned, now), 1):
        if a != b:
            turn, role, _ = a.split()
            return f"message {n} ({turn}, {role})"
    if len(pinned) != len(now):
        return f"the number of messages ({len(pinned)} pinned, {len(now)} now)"
    return "a message whose short hash still matches; compare the full message lists"


class ModelInputParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = fixture("model-inputs.json")
        cls.spec, _ = lb.load_spec()

    def test_every_checkpoint_sends_the_messages_the_models_got_on_oct8(self):
        # The replies were drawn on Oct 2 under spec 0.1.2 (drawn-manifest.json). Rendered from that spec (commit
        # c90c401), the ten main-line inputs match these fixtures byte for byte. The three carried branches were drawn
        # from messy-pilot.yaml v0.2, which is not tracked; v0.3 changed check wording only, per its header.
        now = parity.model_inputs(self.spec)
        self.assertEqual(list(now), list(self.pinned["checkpoints"]), "the checkpoints pinned for Oct 8 changed")
        for c, want in self.pinned["checkpoints"].items():
            with self.subTest(checkpoint=c):
                if now[c]["sha256"] != want["sha256"]:
                    self.fail(f"{c}: the model input is not what the models received on Oct 8; it first differs at "
                              f"{first_changed_message(want['messages'], now[c]['messages'])}."
                              + what_changed(self.pinned) + REBUILD)
        # the single figure the infra audit quotes (80d31303f2e9a8b9...), worked out the audit's way
        self.assertEqual(parity.all_inputs_sha(self.spec), self.pinned["all_sha256"])


class GraderPromptParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = fixture("grader-prompts.json")
        cls.spec, _ = lb.load_spec()

    def test_the_template_is_still_grader_v0_3_with_its_recorded_hash(self):
        want, now = self.pinned["template"], {"version": grader.PROMPT_VERSION, "sha": grader.prompt_hash()}
        if now != want:
            self.fail(f"the grader template changed (was {want['version']} {want['sha']}, now {now['version']} "
                      f"{now['sha']}); a new wording needs a new PROMPT_VERSION." + REBUILD)

    def test_every_check_at_every_checkpoint_renders_the_pinned_prompt(self):
        # covers what prompt_hash does not: the polarity words, the N/A and cannot-determine lines, the note,
        # the transcript, and which checks are graded at each checkpoint, in order
        self.assertEqual(self.pinned["fixed_reply"], parity.FIXED_REPLY)
        now = parity.checkpoint_prompts(self.spec)
        for c, want in self.pinned["checkpoints"].items():
            with self.subTest(checkpoint=c):
                gone = first_difference(want, now.get(c, []))
                if gone:
                    self.fail(f"{c}: the grader prompt changed, {gone} (check, prompt hash)."
                              + what_changed(self.pinned) + REBUILD)

    def test_the_saved_oct8_replies_render_the_pinned_grader_prompts(self):
        now = parity.oct8_prompts(self.spec, parity.graded_units())
        for c, want in self.pinned["oct8"].items():
            with self.subTest(checkpoint=c):
                if now[c] != want:
                    self.fail(f"{c}: regrading the {want['verdicts']} saved verdicts would send different prompts "
                              f"({now[c]['verdicts']} verdicts now): the reply as the grader read it changed, or what "
                              "surrounds it." + what_changed(self.pinned) + REBUILD)

    def test_every_item_test_example_renders_the_pinned_prompt(self):
        for mode, conversation in (("without_conversation", None), ("with_conversation", lb.CONVERSATION)):
            with self.subTest(mode=mode):
                gone = first_difference(self.pinned["item_tests"][mode], parity.example_prompts(conversation))
                if gone:
                    self.fail(f"item tests {mode.replace('_', ' ')}: the grader prompt changed, {gone} (example, "
                              "prompt hash)." + what_changed(self.pinned) + REBUILD)


class ScoreParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = fixture("scores.json")
        cls.spec, _ = lb.load_spec()
        cls.units = parity.graded_units()

    def test_every_saved_reply_adds_up_to_its_pinned_score(self):
        want = self.pinned["replies"]
        now = {parity.reply_key(u): u["score"] for u in parity.rescored(self.spec, self.units)}
        moved = [f"{k} was {v}, now {now.get(k)}" for k, v in want.items() if now.get(k) != v]
        moved += [f"{k} is not pinned" for k in now if k not in want]
        if moved:
            self.fail(f"{len(moved)} of {len(want)} reply scores no longer add up from the saved verdicts as on "
                      f"Oct 8; first: {moved[0]}." + what_changed(self.pinned) + REBUILD)
        # and the score saved beside each reply in runs/oct8-graded is the one the fixture holds
        saved = [parity.reply_key(u) for u in self.units if u["score"] != want[parity.reply_key(u)]]
        self.assertEqual(saved, [], "a score saved in runs/oct8-graded differs from the fixture")

    def test_model_scores_re_added_from_the_saved_verdicts(self):
        # Gemini 0.6764, GPT-6 Astra 0.6696, Grok 0.5184; reviewed 0.76, 0.74, 0.59 (scores.md, scores.reviewed.md)
        now = parity.model_scores(self.spec, self.units)
        self.assertEqual(list(now), list(self.pinned["models"]))
        for model, want in self.pinned["models"].items():
            for field, value in want.items():
                with self.subTest(model=model, field=field):
                    if now[model][field] != value:
                        self.fail(f"{model} {field}: was {value}, now {now[model][field]}."
                                  + what_changed(self.pinned) + REBUILD)

    def test_rows_the_spec_fixes_as_na_still_count_until_the_team_decides(self):
        # The spec fixes AO4 as N/A at KM1, KM2, KM5 and KM6, but the runner grades it there and a verdict other than
        # N/A counts in the score. Whether it should is undecided, so today's rule holds until the team decides.
        for c in ("KM1", "KM2", "KM5", "KM6"):
            fixed = parity.fixed_na(self.spec, c)
            if fixed != ["AO4"]:
                self.fail(f"the spec's always_on map at {c} now fixes {', '.join(fixed) or 'nothing'} as N/A, where it "
                          "fixed AO4." + what_changed(self.pinned) + UNDECIDED)
            if "AO4" not in self.spec.items_at(c):
                self.fail(f"AO4 is no longer graded at {c}, where the spec fixes it as N/A." + UNDECIDED)
        want, now = self.pinned["fixed_na"], parity.fixed_na_rows(self.spec, self.units)
        if (now["rows"], now["counted"]) != (want["rows"], want["counted"]):
            self.fail(f"the rows the spec fixes as N/A changed: {now['rows']} rows, {len(now['counted'])} counted "
                      f"(pinned {want['rows']} and {len(want['counted'])})." + what_changed(self.pinned) + UNDECIDED)
        if now["if_left_out"] != want["if_left_out"]:
            self.fail(f"the scores with those rows left out moved: was {want['if_left_out']}, now {now['if_left_out']}."
                      + what_changed(self.pinned) + REBUILD)
        scored = {parity.reply_key(u): u for u in parity.rescored(self.spec, self.units)}
        for row in want["counted"]:  # "<checkpoint> <model> #<sample> <item> <verdict>"
            key, item = row.rsplit(" ", 2)[0], row.split()[-2]
            without = reply_score([(self.spec.items[v["item"]], v["verdict"]) for v in scored[key]["verdicts"]
                                   if v["item"] != item])
            if scored[key]["score"] == without:
                self.fail(f"{key}: its {item} verdict no longer counts in the reply's score." + UNDECIDED)

    def test_no_gate_tripped_on_oct8_so_a_gate_cap_would_not_move_these_scores(self):
        # The gate cap is adopted (docs/decisions.md 2026-09-27) but applied only when a run asks (bcb.gates, off by
        # default). On Oct 8 the grader called KM7-G1 not met in all 27 replies, so pass^3 holds for every model and
        # the cap must leave these scores and this ranking alone (tests/test_results.py checks it does).
        now = parity.gate_verdicts(self.spec, self.units)
        if now != self.pinned["gates"]:
            self.fail(f"the gate verdicts on the saved replies changed: was {self.pinned['gates']}, now {now}."
                      + what_changed(self.pinned) + REBUILD)


class CompareParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = fixture("compare-outputs.json")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.now = parity.rerun_compare(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, names):
        for name in names:
            with self.subTest(file=name):
                committed = parity.COMMITTED / name
                self.assertEqual(hashlib.sha256(committed.read_bytes()).hexdigest(), self.pinned["files"][name],
                                 f"the committed {committed.relative_to(parity.ROOT)} is not the one the fixture pinned")
                if self.now[name] != self.pinned["files"][name]:
                    old, new = committed.read_text().splitlines(), (self.out / name).read_text().splitlines()
                    gone = first_difference(old, new) or "same lines, different bytes (line endings or the final newline)"
                    self.fail(f"{name} no longer comes out byte for byte: {gone}."
                              + what_changed(self.pinned) + REBUILD)

    def test_compare_rewrites_the_committed_outputs_byte_for_byte(self):
        self.check(parity.COMPARE_FILES)

    def test_review_import_rewrites_the_committed_reviewed_outputs_byte_for_byte(self):
        # run here, not in setUpClass, so a refusal fails this test alone and the compare test still shows its diff
        try:
            self.now = dict(self.now, **parity.rerun_review_import(self.out))
        except RuntimeError as e:
            self.fail(f"{e}." + what_changed(self.pinned) + REBUILD)
        self.check(parity.REVIEW_FILES)


if __name__ == "__main__":
    unittest.main()
