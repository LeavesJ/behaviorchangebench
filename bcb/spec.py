"""Read a conversation spec and render what the model under test and the grader see.

Until step 1 has generated the conversation, the earlier assistant turns exist only as
beats in the spec. `render` then shows each one as a marked placeholder describing what
the turn does, so a draft prefix is never mistaken for the frozen conversation.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FACT_ID = re.compile(r"\bF\d\d\b")
# a beat cites facts as "(F02)", "(F05, F06)" or inside other text, "(... chewing; F25)"
_FACT_CITE = re.compile(r"\s*\((?:F\d\d(?:,\s*)?)+\)|[;,]?\s*\bF\d\d\b")
PLACEHOLDER = "[Draft placeholder: this assistant turn has not been generated yet. What it does: {beat}]"


@dataclass(frozen=True)
class Item:
    id: str
    criterion: str
    polarity: str  # desired | undesired
    type: str  # fidelity | gate
    applies_when: str
    not_applicable: str | None
    points: int | None
    default_when_unsure: str  # the item's own tie-break, else criterion_defaults by type and polarity


@dataclass(frozen=True)
class Turn:
    id: str
    role: str  # user | assistant
    text: str
    header: str | None  # the session's date line, on its first user turn only


class Spec:
    def __init__(self, path, conversation=None):
        self.path = Path(path)
        self.text = self.path.read_text()
        self.raw = yaml.safe_load(self.text)
        self.version = self.raw["spec"]["version"]
        self.moments = {k["id"]: k for k in self.raw["key_moments"]}
        self.items = self._items()
        # step 1's output, {turn id: text}; None means render beats as placeholders
        self.generated = conversation
        edited = self.pinned_mismatches(conversation or {})
        if edited:
            raise ValueError(f"the conversation changes the pinned key-moment lines at {', '.join(edited)}; "
                             "pinned lines are the spec's, byte for byte")

    def _items(self):
        defaults = self.raw["criterion_defaults"]
        unsure = defaults["default_when_unsure"]
        items = {}
        for a in self.raw["always_on_metrics"]:
            items[a["id"]] = Item(a["id"], a["criterion"], a["polarity"], "fidelity",
                                  a.get("applies_when", "always"), a.get("not_applicable"), a.get("points"),
                                  a.get("default_when_unsure", unsure[a["polarity"]]))
        for moment in self.raw["key_moments"]:
            for c in moment.get("criteria") or []:
                kind = c.get("type", defaults["type"])
                items[c["id"]] = Item(c["id"], c["criterion"], c["polarity"], kind,
                                      c.get("applies_when", defaults["scope"]["applies_when"]),
                                      c.get("not_applicable"), c.get("points"),
                                      c.get("default_when_unsure", unsure["gate" if kind == "gate" else c["polarity"]]))
        return items

    def checkpoints(self, include_optional=False):
        return [k["id"] for k in self.raw["key_moments"] if include_optional or k.get("status") != "optional"]

    def items_at(self, checkpoint):
        """Item IDs graded at a checkpoint: the always-on checks, then its own criteria."""
        moment = self.moments[checkpoint]
        ids = [a["id"] for a in self.raw["always_on_metrics"]]
        ids += [c["id"] for c in moment.get("criteria") or []]
        ids += moment.get("criteria_reused") or []
        ids += [i for i in (moment.get("gate_check") or {}) if i not in ids]
        return ids

    def grader_note(self, checkpoint):
        target = self.raw["target"]
        return self.raw["grading"]["grader_note_template"].format(
            target_behavior=target["target_behavior"],
            goal_at_checkpoint=target["goal_at_checkpoint"][checkpoint])

    def turns(self, checkpoint):
        """Every turn the model under test sees at a checkpoint, ending with the user's key line."""
        moment = self.moments[checkpoint]
        prefix = moment.get("prefix")
        if prefix:  # a variant: the base conversation through one turn, then its own user line
            stop, (replaced, line) = prefix["through"], next(iter(prefix["replace_turn"].items()))
            line = moment["user_line"] if line == "user_line" else line
        else:
            stop, replaced, line = moment["checkpoint_turn"], None, None
        pinned = self.pinned_lines()
        # a variant line that replaces a session's opening message keeps that session's date line
        opens = {next(t["id"] for t in s["turns"] if t["role"] == "user"): s["when"]["header"]
                 for s in self.raw["sessions"]}
        out = []
        for session in self.raw["sessions"]:
            header = session["when"]["header"]  # goes on the session's first user turn
            for t in session["turns"]:
                user = t["role"] == "user"
                out.append(Turn(t["id"], t["role"], self._text(t, pinned), header if user else None))
                header = None if user else header
                if t["id"] == stop:
                    if replaced:
                        out.append(Turn(replaced, "user", line, opens.get(replaced)))
                    return out
        raise KeyError(f"{checkpoint}: turn {stop} not found in sessions")

    def pinned_lines(self):
        """{turn id: key-moment user line} for the base conversation; variants (KM7-T, KM7-P) pin nothing."""
        return {k["checkpoint_turn"]: k["user_line"] for k in self.raw["key_moments"]
                if not k.get("prefix") and "user_line" in k}

    def pinned_mismatches(self, texts):
        """Turn IDs whose text in `texts` differs from the spec's pinned line."""
        pinned = self.pinned_lines()
        return sorted(t for t in texts if t in pinned and texts[t] != pinned[t])

    def _text(self, turn, pinned):
        if turn["id"] in pinned:  # the spec's line, whatever a conversation file says
            return pinned[turn["id"]]
        if self.generated and turn["id"] in self.generated:
            return self.generated[turn["id"]]
        if turn["role"] == "user":
            return pinned[turn["id"]] if turn.get("pinned") else turn["draft"]
        return PLACEHOLDER.format(beat=_FACT_CITE.sub("", turn["beat"]).strip())

    def messages(self, checkpoint):
        """The chat-API message list for the model under test; a session's date line opens its first user message."""
        return [{"role": t.role, "content": f"{t.header}\n{t.text}" if t.header else t.text}
                for t in self.turns(checkpoint)]

    def transcript(self, checkpoint):
        """The conversation as the grader sees it: 'role: content' blocks, headers exactly as the model saw them."""
        return "\n\n".join(f"{m['role']}: {m['content']}" for m in self.messages(checkpoint))

    def is_draft_prefix(self, checkpoint):
        return any(t.role == "assistant" and t.text.startswith("[Draft placeholder") for t in self.turns(checkpoint))


def load_conversation(path):
    """Step 1's conversation file (a draft or the frozen v1.0) as {turn id: text}, the form `Spec` takes."""
    return {t["id"]: t["text"] for t in yaml.safe_load(Path(path).read_text())["turns"]}
