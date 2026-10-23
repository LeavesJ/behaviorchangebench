"""What the model under test and the grader see, built from the new files (a conversation file and rubric checks).

The rules are bcb/spec.py's, so a conversation converted from spec.yaml renders byte for byte what it rendered
before (infra audit 4.4): a session's date line opens its first user message; a checkpoint's input is every turn
through the user turn it ends on; a branch is the conversation through `prefix_through`, then its own turns, one or
more, in order (infra audit task 5). A branch's date line, if it has one, opens its first user turn as a new
session's would; one without goes on in the session it leaves, and takes that session's date line if it leaves
before the line was shown. The grader prompt is built by bcb.grader itself, from a `View` that offers what it reads
from a Spec.

Which checks apply at a checkpoint is not decided here but in bcb.loader; callers pass the check.
"""

from . import grader
from .spec import Item

# the two phrases bcb.grader reads as "no condition", so it offers no not_applicable; a check without `when` gets the
# first if it applies always and the second if it names its checkpoints, as spec.yaml's defaults did
NO_CONDITION = {True: "always", False: "always at this checkpoint"}


def turns(scenario, checkpoint_id):
    """[(turn, date line or None)]: every turn the model under test sees at a checkpoint, ending on the user turn
    it answers. A date line goes only on the first user turn of a session, or of a branch that has one or that
    leaves a session before the session's first user turn."""
    c = scenario.checkpoint(checkpoint_id)
    if c.branch is None:
        return _through(scenario, c.at)[0]
    out, waiting = _through(scenario, c.branch.prefix_through)
    # A branch with no date line of its own goes on in the session it leaves. If it leaves before that session's
    # first user turn, the session's date line has not been shown yet, and the branch's first user turn takes it, as
    # bcb.spec gives it to a variant line that replaces the session's opening message.
    return out + _with_header(c.branch.turns, c.branch.header if c.branch.header is not None else waiting)


def _through(scenario, stop):
    """(every turn up to and including `stop`, each with its date line; the date line of stop's session if none of
    its user turns up to `stop` has shown it, else None)."""
    out = []
    for s in scenario.sessions:
        dated = _with_header(s.turns, s.header)
        for n, (t, header) in enumerate(dated):
            out.append((t, header))
            if t.id == stop:
                shown = any(u.role == "user" for u, _ in dated[:n + 1])
                return out, None if shown else s.header
    raise KeyError(f"scenario {scenario.id}: turn {stop} is not in its sessions")


def _with_header(turns, header):
    out = []
    for t in turns:
        user = t.role == "user"
        out.append((t, header if user else None))
        header = None if user else header
    return out


def messages(scenario, checkpoint_id):
    """The chat-API message list for the model under test at a checkpoint."""
    if scenario.system_prompt is not None:
        # nothing sends one yet (infra audit 2.2), and a prompt left out without a word would change what is measured
        raise ValueError(f"scenario {scenario.id} has a system_prompt, which no runner sends yet; leave it out until "
                         "one does")
    return [{"role": t.role, "content": f"{header}\n{t.text}" if header else t.text}
            for t, header in turns(scenario, checkpoint_id)]


def transcript(scenario, checkpoint_id):
    """The conversation as the grader sees it: 'role: content' blocks, date lines exactly as the model saw them."""
    return "\n\n".join(f"{m['role']}: {m['content']}" for m in messages(scenario, checkpoint_id))


def grader_note(scenario, checkpoint_id):
    """The context line at the top of every grader prompt. spec.yaml wrote it as a template with US and 21 fixed in
    it; here jurisdiction and age come from the conversation file, and a sentence whose field is missing is left out."""
    parts = [f"Target behavior: {scenario.target_behavior}.",
             f"User goal at this point: {scenario.checkpoint(checkpoint_id).goal}."]
    if scenario.jurisdiction is not None:
        parts.append(f"Jurisdiction: {scenario.jurisdiction}.")
    if scenario.persona.age is not None:
        parts.append(f"User age: {scenario.persona.age}.")
    return " ".join(parts)


def item(check):
    """A rubric check as the Item that bcb.grader and bcb.score read."""
    applies = check.applies
    return Item(check.id, check.criterion, check.polarity, check.type,
                applies.when if applies.when is not None else NO_CONDITION[applies.always], applies.na_code,
                check.points, check.default_when_unsure)


class View:
    """A conversation file and its checks in the shape bcb.grader.render_prompt reads from a Spec (grader_note,
    transcript, items), so the grader prompt comes from the same code whichever files it was written in."""

    def __init__(self, scenario, checks=()):
        self.scenario = scenario
        self.items = {c.id: item(c) for c in checks}

    def grader_note(self, checkpoint_id):
        return grader_note(self.scenario, checkpoint_id)

    def transcript(self, checkpoint_id):
        return transcript(self.scenario, checkpoint_id)

    def messages(self, checkpoint_id):
        return messages(self.scenario, checkpoint_id)


def prompt(scenario, checkpoint_id, check, reply):
    """The grader prompt for one check on one reply at a checkpoint."""
    return grader.render_prompt(View(scenario), checkpoint_id, item(check), reply)
