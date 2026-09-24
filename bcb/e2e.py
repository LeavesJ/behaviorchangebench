"""The smallest end-to-end run: one model replies at one checkpoint, one grader grades one item.

    python -m bcb.e2e --model openrouter:<model-id> --grader deepseek:deepseek-flash --checkpoint KM1 --item KM1-C3

It proves the keys, the rendering, the grader prompt and the parser work together. It is a
plumbing check, not a result: until step 1 has generated the conversation, the earlier
assistant turns the model sees are beat placeholders.
"""

import argparse
import json
import sys

from . import client, grader
from .item_tests import DEFAULT_SPEC
from .spec import Spec


def main(argv=None, call=client.chat):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--model", required=True, help="model under test, provider:model")
    p.add_argument("--grader", required=True, help="grader, provider:model; never the model's family (README rule 5)")
    p.add_argument("--checkpoint", default="KM1")
    p.add_argument("--item", default="KM1-C3")
    a = p.parse_args(argv)

    spec = Spec(DEFAULT_SPEC)
    if a.item not in spec.items_at(a.checkpoint):
        p.error(f"{a.item} is not graded at {a.checkpoint}")
    if spec.is_draft_prefix(a.checkpoint):
        print("note: earlier assistant turns are beat placeholders (conversation not generated yet)", file=sys.stderr)
    item = spec.items[a.item]
    try:
        reply = call(a.model, spec.messages(a.checkpoint), temperature=1.0, max_tokens=1500)
        prompt = grader.render_prompt(spec, a.checkpoint, item, reply["text"])
        graded = call(a.grader, [{"role": "user", "content": prompt}], temperature=0.0, json_mode=True)
        v = grader.parse(graded["text"], item, reply["text"])
    except (client.CallError, grader.GradeError) as e:
        print(f"e2e failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"checkpoint": a.checkpoint, "model": reply["model"], "reply": reply["text"],
                      "item": item.id, "criterion": item.criterion, "grader": graded["model"],
                      "verdict": v.verdict, "passes": grader.passes(item, v.verdict), "quote": v.quote,
                      "reasoning": v.reasoning}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
