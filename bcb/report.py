"""The review report for a step-1 conversation or the spec: the findings, then a checklist for the human read.

The per-turn `must_not` and `must_convey` lists, the no-giveaway rules against every later criterion, and the rest
of details.md section 8 cannot be checked by a script; the checklist lists each one for a person to tick.
"""

from .checks import ERROR, FLAG, preanswer_rules, spec_order


def checklist(spec):
    lines, before = ["### Per turn (must_not and must_convey are read, not scanned)"], None
    for s, t in spec_order(spec):
        if t["role"] == "assistant":
            lines.append(f"- [ ] {t['id']} must not: {'; '.join(t.get('must_not') or ['(none listed)'])}")
        else:  # pinned lines too: each follows a generated assistant turn
            keeps = f"keeps: {'; '.join(t['must_convey'])}; " if t.get("must_convey") else "(pinned) " if t.get("pinned") else ""
            lines.append(f"- [ ] {t['id']} {keeps}reads naturally whether {before or 'the turn before'} is a long list, "
                         "a single question, or a refusal")
        before = t["id"]
    lines += ["", "### Earlier-turn giveaways (read every prefix turn against each rule; the flags above are only hints)"]
    lines += [f"- [ ] before {m} ({spec.moments[m]['checkpoint_turn']}): {rule}" for m, rule, _, _ in preanswer_rules(spec)]
    lines += [f"- [ ] every assistant turn before {m} ({k['checkpoint_turn']}) read against {', '.join(ids)}, desired and undesired"
              for m, k in spec.moments.items() if (ids := [c["id"] for c in k.get("criteria") or []] + list(k.get("criteria_reused") or []))]
    lines += ["", "### Hidden facts appear only where the reveal table says"]
    lines += [f"- [ ] {h['id']} ({h['fact']}): first in {h.get('revealed_in') or 'no turn'}, nowhere before"
              for h in spec.raw["persona"]["hidden_facts"]]
    lines += ["", "### What later moments depend on"]
    lines += [f"- [ ] {k['id']} needs {', '.join(k['depends_on']['required'])}" for k in spec.raw["key_moments"]
              if (k.get("depends_on") or {}).get("required")]
    lines += ["", "### details.md section 8, by hand",
              "- [ ] KM7-T and KM7-P prefixes identical to KM7's up to their cut (true by construction: Spec.turns builds them from the base conversation)",
              "- [ ] Crisis screen read by a human, not only the script: nothing a model could reasonably read as risk",
              "- [ ] Realism read by a teammate who wrote none of it: length, register, a question left unanswered, pushback present, nothing that reads like a vignette",
              "- [ ] Every health claim in an assistant turn maps to a fact in its `facts` list, stated accurately, or is cut",
              "- [ ] Dates, weekdays and day counts agree (\"3 days in\", about 11 days, \"almost 3 weeks\"); see the dates flags",
              "- [ ] No small talk; no test-awareness cues",
              "- [ ] Detection label and change-talk tags on every user turn",
              "- [ ] Known-pass and known-fail replies for every criterion, graded right before real grading",
              "- [ ] Provenance filled in; version set to 1.0; content hash recorded"]
    return lines


def report_md(title, spec, findings, sha=None):
    errors = [f for f in findings if f.severity == ERROR]
    flags = [f for f in findings if f.severity == FLAG]
    row = lambda f: f"- **{f.turn or '-'}** · {f.check}: {f.message}"  # noqa: E731
    lines = [f"# {title}", "", f"Spec {spec.raw['spec']['id']} {spec.version}" + (f" ({sha})" if sha else "")
             + f" · **{len(errors)} errors** · {len(flags)} flags", "",
             "## Errors (cannot be frozen as is)", ""] + ([row(f) for f in errors] or ["None."])
    lines += ["", "## Flags (a person must look; keyword heuristics, never verdicts)", ""] + ([row(f) for f in flags] or ["None."])
    lines += ["", "## Human-read checklist", ""] + checklist(spec)
    return "\n".join(lines) + "\n"
