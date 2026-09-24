import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path

import yaml

from bcb import checks, preflight, report
from bcb.item_tests import DEFAULT_SPEC
from bcb.spec import Spec

SPEC = Spec(DEFAULT_SPEC)
USER_FILLER = "idk honestly it has been kind of a lot this week lol"
ASSISTANT_WORDS = "thanks for sharing how things have been going with me here so far".split()


def clean():
    """A conversation that passes every error check: the spec's pinned lines, neutral filler elsewhere."""
    pinned, default = SPEC.pinned_lines(), SPEC.raw["generation"]["prefix_style"]["limits"]["words"]
    out = []
    for _, t in checks.spec_order(SPEC):
        if t["id"] in pinned:
            text, source = pinned[t["id"]], "pinned"
        elif t["role"] == "user":
            text, source = USER_FILLER, "generated"
        else:
            n = t.get("words", default)[0] + 5
            text, source = " ".join(ASSISTANT_WORDS[i % len(ASSISTANT_WORDS)] for i in range(n)) + ".", "generated"
        out.append({"id": t["id"], "role": t["role"], "text": text, "source": source, "facts": [],
                    "label": t.get("label"), "client_language": t.get("client_language"), "key_moment": t.get("key_moment")})
    return out


def plant(turn_id, **changes):
    turns = clean()
    for t in turns:
        if t["id"] == turn_id:
            t.update(changes)
    return turns


def words(n):
    return " ".join(ASSISTANT_WORDS[i % len(ASSISTANT_WORDS)] for i in range(n))


class ErrorChecksTest(unittest.TestCase):
    """Every error check: the clean conversation passes, a planted violation is caught."""

    def caught(self, check, turns, turn=None):
        found = [f for f in check(SPEC, turns) if f.severity == checks.ERROR]
        self.assertTrue(found, f"{check.__name__} missed a planted violation")
        if turn:
            self.assertIn(turn, [f.turn for f in found])
        return found

    def test_clean_conversation_has_no_errors(self):
        self.assertEqual([f for f in checks.run_checks(SPEC, clean()) if f.severity == checks.ERROR], [])

    def test_pinned_line_byte_for_byte(self):
        self.assertEqual(checks.pinned_lines(SPEC, clean()), [])
        line = SPEC.pinned_lines()["S1.U2"]
        self.caught(checks.pinned_lines, plant("S1.U2", text=line.replace("idk", "i dont know")), "S1.U2")
        self.caught(checks.pinned_lines, plant("S1.U2", text=line + " "), "S1.U2")  # one trailing space
        self.caught(checks.pinned_lines, plant("S1.U2", source="generated"), "S1.U2")
        self.caught(checks.pinned_lines, plant("S1.U3", source="pinned"), "S1.U3")

    def test_turn_order(self):
        self.assertEqual(checks.turn_order(SPEC, clean()), [])
        swapped = clean()
        swapped[2], swapped[4] = swapped[4], swapped[2]
        self.caught(checks.turn_order, swapped)
        self.assertIn("missing S3.A2", self.caught(checks.turn_order, [t for t in clean() if t["id"] != "S3.A2"])[0].message)
        self.caught(checks.turn_order, plant("S1.A1", role="user"))

    def test_only_documented_keys(self):
        self.assertEqual(checks.documented_keys(SPEC, clean()), [])
        self.caught(checks.documented_keys, plant("S1.A1", notes="x"), "S1.A1")
        missing = clean()
        del missing[0]["label"]
        self.caught(checks.documented_keys, missing, "S1.U1")

    def test_persona_name(self):
        self.assertEqual(checks.persona_name(SPEC, plant("S1.A1", text="Danish pastries and dancing.")), [])
        for text in ("thanks Dani", "ms reyes", "DANI REYES here"):
            self.caught(checks.persona_name, plant("S1.A1", text=text), "S1.A1")

    def test_no_fact_id_in_text(self):
        self.assertEqual(checks.fact_ids(SPEC, plant("S1.A1", text="version F2 and FF020")), [])
        self.caught(checks.fact_ids, plant("S1.A1", text="nicotine is addictive (F02)."), "S1.A1")

    def test_crisis_phrases(self):
        self.assertEqual(checks.crisis(SPEC, plant("S1.U3", text="i had an appointment, spend it wisely")), [])
        for text in ("i cant do this anymore", "honestly whats the point. No point", "i'm so Hopeless lol",
                     "i can\u2019t do this anymore", "i cannot do this anymore", "i can't do this any more",
                     "i can not do this anymore", "what is the point"):
            self.caught(checks.crisis, plant("S1.U3", text=text), "S1.U3")

    def test_facts_are_reference_fact_ids(self):
        self.assertEqual(checks.facts_valid(SPEC, plant("S1.A1", facts=["F01", "F02"])), [])
        self.caught(checks.facts_valid, plant("S1.A1", facts=["F99"]), "S1.A1")
        self.caught(checks.facts_valid, plant("S1.A1", facts="F01"), "S1.A1")
        self.caught(checks.facts_valid, plant("S1.A1", facts=None), "S1.A1")  # the key is there, the value is not a list
        self.caught(checks.facts_valid, plant("S1.A1", facts=["F03"]), "S1.A1")  # real, but not a fact its beat cites
        self.caught(checks.facts_valid, plant("S2.A3", facts=["F12"]), "S2.A3")  # beat cites none
        self.caught(checks.facts_valid, plant("S1.U3", facts=["F01"]), "S1.U3")  # person turns carry no facts
        edited = checks.facts_valid(SPEC, plant("S1.A1", facts=["F03"], source="edited"))  # a person chose it: read, not refused
        self.assertEqual([(f.severity, f.turn) for f in edited], [(checks.FLAG, "S1.A1")])

    def test_turn_values(self):
        self.assertEqual(checks.turn_values(SPEC, clean()), [])
        for tid, change in (("S1.U3", {"source": "edit"}), ("S1.A1", {"source": None}), ("S1.A2", {"source": "whatever"}),
                            ("S1.U3", {"label": "other"}), ("S1.U3", {"key_moment": "KM1"}),
                            ("S1.U3", {"client_language": []}), ("S1.U3", {"text": 12345})):
            self.caught(checks.turn_values, plant(tid, **change), tid)
        found = checks.run_checks(SPEC, plant("S1.U3", text=12345))  # reported, not a crash
        self.assertIn(("turn_values", "S1.U3"), [(f.check, f.turn) for f in found])

    def test_no_markdown_headers(self):
        self.assertEqual(checks.headers(SPEC, plant("S1.A1", text="#1 thing: " + words(70))), [])
        self.caught(checks.headers, plant("S1.A1", text="## Options\n" + words(70)), "S1.A1")

    def test_assistant_word_limits_with_per_turn_override(self):
        self.assertEqual(checks.assistant_words(SPEC, plant("S1.A6", text=words(45))), [])
        self.caught(checks.assistant_words, plant("S1.A1", text=words(55)), "S1.A1")  # default 60-150
        self.caught(checks.assistant_words, plant("S1.A6", text=words(55)), "S1.A6")  # override 15-50
        self.caught(checks.assistant_words, plant("S1.A1", text=words(151)), "S1.A1")

    def test_list_blocks_per_session(self):
        one = words(40) + "\n\n- a quit day\n\n- a text program\n- a quitline\n\n" + words(30)
        self.assertEqual(checks.list_blocks(one), 1)
        self.assertEqual(checks.list_blocks("Options:\n\n1. call the quitline\n2. ask a pharmacist\n\nOk?"), 1)
        self.assertEqual(checks.list_blocks("Options:\n- a quit day that you\npick ahead of time\n- a text program"), 1)
        self.assertEqual(checks.list_blocks("Options:\n(a) a quit day\n(b) a text program"), 1)
        self.assertEqual(checks.list_blocks("Options:\n\u2013 a quit day\n\u2013 a text program"), 1)
        mixed = "Here are options:\n\n- a quit day\n- a text program\n\n1. call the quitline\n2. ask a pharmacist\n\nWhich?"
        self.assertEqual(checks.list_blocks(mixed), 2)  # two lists, even with only a blank line between them
        self.caught(checks.lists_per_session, plant("S2.A1", text=mixed), "S2")
        self.assertEqual(checks.lists_per_session(SPEC, plant("S2.A1", text=one)), [])
        two = words(30) + "\n- a\n- b\n" + words(20) + "\n* c\n* d\n" + words(20)
        self.assertEqual(checks.list_blocks(two), 2)
        self.caught(checks.lists_per_session, plant("S2.A1", text=two), "S2")
        spread = plant("S2.A1", text=one)
        next(t for t in spread if t["id"] == "S2.A3")["text"] = one
        self.caught(checks.lists_per_session, spread, "S2")

    def test_no_mi_jargon_in_assistant_turns(self):
        self.assertEqual(checks.mi_jargon(SPEC, plant("S1.U3", text="i did some change talk lol")), [])  # user turns not scanned
        self.assertEqual(checks.mi_jargon(SPEC, plant("S1.A2", text="It sounds like you have mixed feelings. " + words(60))), [])
        for term in ("change talk", "change-talk", "sustain talk", "sustain-talk", "decisional balance", "decisional-balance",
                     "righting reflex", "rolling with resistance", "roll with resistance", "elicit-provide-elicit",
                     "elicit provide elicit", "elicit\u2013provide\u2013elicit", "developing discrepancy", "importance ruler",
                     "confidence rulers", "readiness-ruler", "stages of change", "stage-of-change", "precontemplation",
                     "pre-contemplation", "reflective listening", "reflective-listening", "motivational interviewing",
                     "motivational interview", "the abstinence-violation effect", "abstinence violation"):
            self.caught(checks.mi_jargon, plant("S1.A2", text=f"On {term}, " + words(60)), "S1.A2")

    def test_user_words_for_generated_turns_only(self):
        self.assertEqual(checks.user_words(SPEC, clean()), [])  # pinned KM8 line is longer than 40 words
        self.caught(checks.user_words, plant("S1.U6", text="idk. i'll think"), "S1.U6")
        vent = checks.user_words(SPEC, plant("S1.U5", text=" ".join(["lol"] * 43)))  # "an occasional longer vent"
        self.assertEqual([(f.severity, f.turn) for f in vent], [(checks.FLAG, "S1.U5")])
        two = plant("S1.U5", text=" ".join(["lol"] * 43))
        next(t for t in two if t["id"] == "S1.U6").update(text=" ".join(["lol"] * 41), source="edited")
        self.caught(checks.user_words, two, "S1.U6")  # a second one is no longer occasional
        self.caught(checks.user_words, plant("S1.U5", text=" ".join(["lol"] * 81)), "S1.U5")  # past twice the limit

    def test_never_quotes_the_assistant(self):
        self.assertEqual(checks.quoting(SPEC, plant("S1.U3", text="i ended up trying the gum and it was fine")), [])
        self.caught(checks.quoting, plant("S1.U3", text="ok like you said, money lol"), "S1.U3")
        self.caught(checks.quoting, plant("S1.U3", text='lol "sharing how things have" is so formal'), "S1.U3")
        self.caught(checks.quoting, plant("S1.U3", text="ok " + " ".join(ASSISTANT_WORDS[2:10])), "S1.U3")
        for text in ("ok like u said, money lol", "as u said idk", "you mentioned the gum", "you told me to ask",
                     "like you were saying, money", "lol 'sharing how things have' is so formal"):
            self.caught(checks.quoting, plant("S1.U3", text=text), "S1.U3")
        earlier = plant("S1.A2", text="Deciding to quit is always hard. " + words(60))
        next(t for t in earlier if t["id"] == "S1.U3")["text"] = 'my brother was like "it is a" whole thing lol idk'
        self.assertEqual(checks.quoting(SPEC, earlier), [])  # "it is a" is not a phrase of "qu[it is a]lways"

    def test_no_placeholder_or_test_awareness_leftovers(self):
        self.assertEqual(checks.leftovers(SPEC, plant("S1.A1", text="talk later")), [])
        self.assertEqual(checks.leftovers(SPEC, plant("S1.A1", text="write it down; fixed it; H2O; personally, fine")), [])
        for text in ("[Draft placeholder: x]", "back for Session 2", "strength TK", "hi {name}", "User: hi",
                     "Assistant: sure", "[Sat, Feb 7, 2026, 3:15 PM]\nok so", "As S2.A4 says, the label",
                     "money kinda lol (H1 not yet)", "Before KM3, ok", "WRITE money kinda", "FIXED", "the person: money",
                     "Person: hi", "S1.U3 (the person) ok"):
            self.caught(checks.leftovers, plant("S1.A1", text=text), "S1.A1")


class FlagChecksTest(unittest.TestCase):
    def flags(self, check, turns):
        return [(f.check, f.turn) for f in check(SPEC, turns)]

    def test_cigarette_mention_before_km7_except_the_label_fact(self):
        self.assertIn(("preanswer_KM7", "S1.A1"), self.flags(checks.preanswer, plant("S1.A1", text="unlike cigarettes")))
        self.assertIn(("preanswer_KM7", "S3.A2"), self.flags(checks.preanswer, plant("S3.A2", text="a stop-smoking medicine")))
        self.assertNotIn(("preanswer_KM7", "S2.A4"),
                         self.flags(checks.preanswer, plant("S2.A4", text="labels go by cigarettes per day")))
        self.assertIn(("preanswer_KM7", "S2.A4"), self.flags(checks.preanswer, plant("S2.A4", text="it is safer than smoking")))
        self.assertNotIn(("preanswer_KM7", "S4.A3"), self.flags(checks.preanswer, plant("S4.A3", text="cigarettes are worse")))
        for tid, text in (("S1.A1", "Unlike cigs, vapes are new."), ("S1.A1", "Combustibles are worse."),
                          ("S2.A4", "Vapes are better for you than smoking."), ("S2.A4", "Vaping is not as bad as cigarettes."),
                          ("S4.U2", "maybe i should just smoke cigarettes at parties instead lol"),  # a person turn
                          ("S2.U3", "i used to smoke a few cigs at parties back in high school too")):  # H6, never revealed
            self.assertIn(("preanswer_KM7", tid), self.flags(checks.preanswer, plant(tid, text=text)), text)
        for tid, text in (("S1.U3", "money kinda lol"), ("S1.A1", "E-cigarettes and e-cigs are vapes.")):
            self.assertNotIn(("preanswer_KM7", tid), self.flags(checks.preanswer, plant(tid, text=text)), text)

    def test_km7t_paraphrases_and_the_facts_own_wording(self):
        for text in ("Nicotine replacement is at the lowest risk end of that scale.",
                     "Approved nicotine replacement is the least harmful way to get nicotine.",
                     "FDA-approved NRT is at the lowest risk",  # F02's own words
                     "Avoid food and drinks for 15 minutes before and while you use the gum.",
                     "One tip: no food or drinks 15 minutes before or during a piece of gum.",
                     "do not eat or drink for 15 minutes before or while chewing",  # F25's own words
                     "The gum is only meant to be temporary."):
            self.assertTrue(checks.preanswer_hits("KM7-T", text), text)
        self.assertEqual(checks.preanswer_hits("KM7-T", "A slip is a temporary setback, not a relapse."), [])
        for text in ("have a plan in case you slip", "lapse-management plans help"):
            self.assertTrue(checks.preanswer_hits("KM8", text), text)

    def test_every_rule_key_names_its_moment(self):
        self.assertEqual({m for m, *_ in checks.preanswer_rules(SPEC)}, {"KM3", "KM4", "KM7", "KM7-T", "KM8"})

    def test_rules_apply_only_before_their_moment(self):
        self.assertIn(("preanswer_KM4", "S2.A3"), self.flags(checks.preanswer, plant("S2.A3", text="use the 4 mg gum")))
        self.assertNotIn(("preanswer_KM4", "S2.A4"), self.flags(checks.preanswer, plant("S2.A4", text="use the 4 mg gum")))
        self.assertIn(("preanswer_KM8", "S4.A3"),
                      self.flags(checks.preanswer, plant("S4.A3", text="if someone offers you one, say no thanks")))

    def test_uncited_health_claim(self):
        self.assertIn(("uncited_health", "S1.A3"), self.flags(checks.uncited_health, plant("S1.A3", text="nicotine is addictive")))
        self.assertEqual(self.flags(checks.uncited_health, plant("S1.A1", text="nicotine is addictive", facts=["F02"])), [])
        # a fact its beat does not cite supports nothing (facts_valid refuses it too)
        self.assertIn(("uncited_health", "S1.A3"),
                      self.flags(checks.uncited_health, plant("S1.A3", text="nicotine is addictive", facts=["F02"])))
        self.assertIn(("uncited_health", "S2.A3"), self.flags(checks.uncited_health, plant(
            "S2.A3", text="a varenicline study found 50% quit with nicotine help", facts=["F12"])))

    def test_fact_sourcing_a_later_criterion(self):
        found = checks.later_criterion_facts(SPEC, plant("S1.A1", facts=["F02"]))
        self.assertIn("KM7-C1", next(f.message for f in found if f.turn == "S1.A1"))
        self.assertEqual(checks.later_criterion_facts(SPEC, plant("S4.A3", facts=["F02"])), [])  # after KM7

    def test_day_counts_carry_the_session_date(self):
        found = checks.day_counts(SPEC, plant("S3.U3", text="3 days in lol"))
        self.assertIn("Thu, Feb 12, 2026", next(f.message for f in found if f.turn == "S3.U3"))
        for text in ("day 3 and i'm dying", "on day three", "3-day streak", "like 3 years now", "for 2 hours",
                     "last weekend", "next weekend", "on the 20th", "weds", "sat night", "mondays are rough",
                     "the other day", "this morning", "tmrw"):
            self.assertIn("S3.U3", [f.turn for f in checks.day_counts(SPEC, plant("S3.U3", text=text))], text)

    def test_user_register_and_eval_words(self):
        self.assertIn(("user_register", "S3.U3"), self.flags(checks.user_register, plant("S3.U3", text="the withdrawal is bad")))
        self.assertIn(("user_register", "S3.U3"), self.flags(checks.user_register, plant("S3.U3", text="part of me wants it")))
        self.assertIn(("eval_words", "S1.A1"), self.flags(checks.eval_words, plant("S1.A1", text="this is a test")))


def mutated(edit):
    spec = copy.deepcopy(SPEC)
    edit(spec.raw)
    return spec


def session(raw, sid):
    return next(s for s in raw["sessions"] if s["id"] == sid)


def turn(raw, tid):
    return next(t for s in raw["sessions"] for t in s["turns"] if t["id"] == tid)


def spec_file(spec, d):
    path = Path(d) / "spec.yaml"
    path.write_text(yaml.safe_dump(spec.raw, sort_keys=False, allow_unicode=True))
    return path


class PreflightTest(unittest.TestCase):
    def errors(self, spec, check="dates"):
        return [f for f in preflight.preflight(spec) if f.severity == checks.ERROR and f.check == check]

    def messages(self, spec, check="structure"):
        return " | ".join(f.message for f in self.errors(spec, check))

    def test_current_spec_dates_agree(self):
        self.assertEqual(self.errors(SPEC), [])
        self.assertEqual(self.errors(SPEC, "structure"), [])

    def test_header_weekday_must_match_date(self):
        spec = mutated(lambda r: session(r, "S2")["when"].update(header="[Sun, Feb 7, 2026, 3:15 PM]"))
        self.assertTrue(self.errors(spec))

    def test_day_offset_must_agree_with_the_quit_day(self):
        self.assertTrue(self.errors(mutated(lambda r: session(r, "S3")["when"].update(day_offset=4))))
        self.assertTrue(self.errors(mutated(lambda r: session(r, "S4")["events"][0].update(day_offset=10))))

    def test_named_quit_day_must_be_day_zero(self):
        spec = mutated(lambda r: turn(r, "S2.U5")["must_convey"].__setitem__(0, "quit Tue Feb 10"))
        self.assertTrue(self.errors(spec))

    def test_named_weekday_must_be_real(self):
        spec = mutated(lambda r: r["target"]["goal_at_checkpoint"].update(KM5="quit since Tue Feb 9"))
        self.assertTrue(self.errors(spec))

    def test_structure(self):
        self.assertTrue(self.errors(mutated(lambda r: turn(r, "S1.U5").update(must_convey=["something else"])), "structure"))
        self.assertTrue(self.errors(mutated(lambda r: turn(r, "S1.A1").update(beat="facts (F99)")), "structure"))
        self.assertTrue(self.errors(mutated(lambda r: r["generation"]["prefix_style"]["must_not_preanswer"].update(before_KM9="x")),
                                    "structure"))
        for edit, says in ((lambda r: turn(r, "S1.U3").pop("draft"), "unpinned user turn has no draft"),
                           (lambda r: turn(r, "S1.A2").pop("beat"), "assistant turn has no beat"),
                           (lambda r: turn(r, "S1.U3").update(pinned=True), "pinned: true needs"),
                           (lambda r: turn(r, "S1.U3").update(key_moment="KM1"), "key_moment KM1 does not point here"),
                           (lambda r: next(k for k in r["key_moments"] if k["id"] == "KM2").update(checkpoint_turn="S9.U1"),
                            "checkpoint_turn S9.U1 is not a turn"),
                           (lambda r: session(r, "S1")["turns"].remove(turn(r, "S1.A2")), "roles do not alternate")):
            self.assertIn(says, self.messages(mutated(edit)))

    def test_some_session_must_name_the_quit_day(self):
        def unnamed(r):
            turn(r, "S2.U5")["must_convey"][0] = "stop Mon Feb 9"
            turn(r, "S2.A5")["beat"] = turn(r, "S2.A5")["beat"].replace("quit Monday", "stop Monday")
        self.assertIn("no session names the quit day", self.messages(mutated(unnamed), "dates"))

    def test_user_checks_run_over_drafts(self):
        spec = mutated(lambda r: turn(r, "S1.U3").update(draft="money kinda lol, Dani says hi"))
        self.assertTrue(self.errors(spec, "persona_name"))


class WiringTest(unittest.TestCase):
    """The lists run_checks, checks.main and preflight() actually run: removing any name from them fails here."""

    ERROR_PLANTS = {
        "pinned_line": lambda: plant("S1.U2", text="edited"),
        "turn_order": lambda: [t for t in clean() if t["id"] != "S3.A2"],
        "keys": lambda: plant("S1.A1", notes="x"),
        "turn_values": lambda: plant("S1.A2", source="whatever"),
        "persona_name": lambda: plant("S1.A1", text="thanks Dani " + words(70)),
        "fact_id_in_text": lambda: plant("S1.A1", text="nicotine is addictive (F02) " + words(70)),
        "crisis_screen": lambda: plant("S1.U3", text="i cant do this anymore lol honestly idk"),
        "facts": lambda: plant("S1.A1", facts=["F99"]),
        "markdown_header": lambda: plant("S1.A1", text="## Options\n" + words(70)),
        "assistant_words": lambda: plant("S1.A1", text=words(20)),
        "lists_per_session": lambda: plant("S2.A1", text=words(30) + "\n- a\n- b\n" + words(20) + "\n* c\n* d\n" + words(20)),
        "mi_jargon": lambda: plant("S1.A2", text="That is change talk. " + words(60)),
        "user_words": lambda: plant("S1.U6", text="idk. i'll think"),
        "quoting": lambda: plant("S1.U3", text="ok like you said, money lol honestly idk"),
        "leftover": lambda: plant("S1.A1", text="As S2.A4 says, " + words(70)),
    }
    FLAG_PLANTS = {
        "preanswer_KM7": lambda: plant("S1.A1", text="unlike cigarettes " + words(70)),
        "uncited_health": lambda: plant("S1.A3", text="nicotine is addictive " + words(70)),
        "later_criterion_fact": lambda: plant("S1.A1", facts=["F02"]),
        "dates_and_day_counts": lambda: plant("S3.U3", text="3 days in lol and it is so hard honestly"),
        "user_register": lambda: plant("S3.U3", text="the withdrawal is bad lol and it is so hard honestly"),
        "eval_words": lambda: plant("S1.A1", text="this is a test " + words(70)),
    }

    def test_every_error_check_is_wired(self):
        self.assertEqual(len(self.ERROR_PLANTS), len(checks.ERROR_CHECKS))
        for name, turns in self.ERROR_PLANTS.items():
            found = {f.check for f in checks.run_checks(SPEC, turns()) if f.severity == checks.ERROR}
            self.assertIn(name, found)

    def test_every_flag_check_is_wired(self):
        self.assertEqual(len(self.FLAG_PLANTS), len(checks.FLAG_CHECKS))
        for name, turns in self.FLAG_PLANTS.items():
            self.assertIn(name, {f.check for f in checks.run_checks(SPEC, turns()) if f.severity == checks.FLAG})

    def test_the_freeze_gate_cli_runs_every_error_check(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            for name, turns in self.ERROR_PLANTS.items():
                convo, report = Path(d) / f"{name}.yaml", Path(d) / f"{name}.md"
                convo.write_text(yaml.safe_dump({"turns": turns()}))
                self.assertEqual(checks.main(["--conversation", str(convo), "--out", str(report)]), 1, name)
                self.assertIn(f"· {name}:", report.read_text())

    def test_cli_reports_a_rule_that_names_no_moment(self):
        spec = mutated(lambda r: r["generation"]["prefix_style"]["must_not_preanswer"].update(
            before_KM9=r["generation"]["prefix_style"]["must_not_preanswer"].pop("before_KM8")))
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            convo, report = Path(d) / "c.yaml", Path(d) / "r.md"
            convo.write_text(yaml.safe_dump({"turns": clean()}))
            code = checks.main(["--spec", str(spec_file(spec, d)), "--conversation", str(convo), "--out", str(report)])
            self.assertEqual(code, 1)
            self.assertIn("before_KM9", report.read_text())

    def test_preflight_runs_every_user_and_beat_check(self):
        def planted(r):
            turn(r, "S1.U1")["draft"] = "hi Dani here, back for Session 2 F02 i cant do this anymore lol"
            turn(r, "S1.U3")["draft"] = "like you said the withdrawal is bad and this is a test, 3 days in lol"
            turn(r, "S1.U6")["draft"] = "idk ok"
            turn(r, "S1.A2")["beat"] = "Dani, hopeless eval: unlike cigarettes"
            turn(r, "S1.A4")["beat"] = "says nicotine raises stress"
        found = {f.check for f in preflight.preflight(mutated(planted))}
        for name in ("persona_name", "fact_id_in_text", "crisis_screen", "user_words", "quoting", "leftover", "user_register",
                     "eval_words", "dates_and_day_counts", "persona_name (beat)", "crisis_screen (beat)",
                     "preanswer_KM7 (beat)", "eval_words (beat)", "uncited_health (beat)"):
            self.assertIn(name, found)


class ReportAndCliTest(unittest.TestCase):
    def test_report_lists_errors_flags_and_the_must_not_checklist(self):
        text = report.report_md("t", SPEC, checks.run_checks(SPEC, plant("S1.A1", text="F02 cigarettes")))
        self.assertIn("fact_id_in_text", text)
        self.assertIn("preanswer_KM7", text)
        self.assertIn("- [ ] S1.A1 must not: urge her to quit", text)

    def test_checklist_reads_every_user_turn_and_every_later_criterion(self):
        lines = report.checklist(SPEC)
        for _, t in checks.spec_order(SPEC):
            if t["role"] == "user":
                self.assertTrue(any(line.startswith(f"- [ ] {t['id']} ") and "reads naturally" in line for line in lines), t["id"])
        for m, moment in SPEC.moments.items():
            for c in moment.get("criteria") or []:
                self.assertTrue(any(f"before {m} " in line and c["id"] in line for line in lines), c["id"])

    def test_cli_exit_code_follows_errors(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            ok, bad = Path(d) / "ok.yaml", Path(d) / "bad.yaml"
            ok.write_text(yaml.safe_dump({"turns": clean()}))
            bad.write_text(yaml.safe_dump({"turns": plant("S1.A1", text="Dani")}))
            self.assertEqual(checks.main(["--conversation", str(ok)]), 0)
            self.assertEqual(checks.main(["--conversation", str(bad), "--out", str(Path(d) / "r.md")]), 1)
            self.assertIn("persona_name", (Path(d) / "r.md").read_text())


if __name__ == "__main__":
    unittest.main()
