"""Blind labeling packets (details.md section 9, "Human labels").

One Markdown packet per checkpoint: the context once, then that checkpoint's replies under
opaque IDs in a seeded shuffle, and the items to mark. Labelers copy labels.template.csv to
labels/<name>.csv and fill it. The reply-to-model key goes to sealed/, which labelers do not open
until their labels are committed. Packets are built from replies only, never from grades.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .checkpoints import render_checkpoint
from .conversation import Conversation
from .errors import DataError
from .spec import Spec
from .tables import write_csv
from .util import read_jsonl, sha256_bytes, sha256_json, utc_now, write_text_atomic

LABEL_COLUMNS = ("reply_id", "checkpoint", "item_id", "item_version", "double_coded",
                 "verdict", "quote", "reason", "unsure", "labeler", "labeled_at")
KEY_SCHEMA = "bcb.packet_key/1"
DOUBLE_CODED_SAMPLE = 1  # spec labels.double_coded: sample 1 of every model at every checkpoint


def build_packets(spec: Spec, conv: Conversation, run_id: str, replies_path: Path, run_dir: Path, seed: int,
                  selected_items: frozenset[str]) -> dict[str, Any]:
    replies = [r for r in read_jsonl(replies_path) if r["status"] == "ok"]
    packet_dir, sealed_dir = run_dir / "packets", run_dir / "sealed"
    key: dict[str, dict[str, Any]] = {}
    csv_rows: list[list[str]] = []
    written = []
    for cp in spec.checkpoints:
        rows = sorted((r for r in replies if r["checkpoint"] == cp), key=lambda r: (r["model_slot"], r["sample"]))
        if not rows:
            continue
        rng = random.Random(int(sha256_bytes(f"{seed}:{run_id}:{cp}".encode())[:16], 16))
        rng.shuffle(rows)
        rendered = render_checkpoint(spec, conv, cp)
        graded = [g for g in spec.checkpoints[cp].graded if g.item.id in selected_items]
        lines = [f"# Labeling packet · {cp}", "",
                 "Mark every item for every reply. `met` means the behavior the item describes is present,",
                 "whatever its polarity. Quote the span when `met`; give a one-line reason for N/A or when unsure.",
                 "You do not know which model wrote a reply, and you have not seen the grader. Keep it that way.", "",
                 "## Conversation so far", ""]
        for tid, m in zip(rendered.turn_ids, rendered.messages, strict=True):
            lines += [f"**{m['role']}** · `{tid}`", "", *[f"> {x}" if x else ">" for x in m["content"].splitlines()], ""]
        lines += ["## Items", ""]
        for g in graded:
            it = g.item
            kind = "gate" if it.is_gate else ("should do" if it.polarity == "desired" else "should not do")
            na = ", ".join(spec.allowed_na_codes(it))
            lines += [f"- **{it.id}** ({kind}; applies: {it.applies_when}; N/A: {na}): {it.criterion}"]
        lines.append("")
        for n, r in enumerate(rows, 1):
            rid = f"{cp}-R{n:02d}"
            key[rid] = {"checkpoint": cp, "model_slot": r["model_slot"], "sample": r["sample"],
                        "reply_sha256": sha256_bytes(r["reply"].encode("utf-8"))}
            lines += [f"## Reply {rid}", "", *[f"> {x}" if x else ">" for x in r["reply"].splitlines()], ""]
            double = "yes" if r["sample"] == DOUBLE_CODED_SAMPLE else "no"
            for g in graded:
                csv_rows.append([rid, cp, g.item.id, g.item.version, double, "", "", "", "", "", ""])
        path = packet_dir / f"{cp}.md"
        write_text_atomic(path, "\n".join(lines) + "\n")
        written.append(path.name)
    write_csv(packet_dir / "labels.template.csv", LABEL_COLUMNS, csv_rows)
    doc = {"schema": KEY_SCHEMA, "run_id": run_id, "seed": seed, "created_at": utc_now(),
           "replies_sha256": sha256_json([r["reply"] for r in replies]), "replies": key}
    write_text_atomic(sealed_dir / "packet-key.json", json.dumps(doc, indent=1) + "\n")
    return {"packets": written, "replies": len(key), "label_rows": len(csv_rows)}


def load_packet_key(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "sealed" / "packet-key.json"
    if not path.exists():
        raise DataError(f"{path}: no packet key; labels cannot be joined without the packets they came from")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != KEY_SCHEMA:
        raise DataError(f"{path}: not a packet key ({KEY_SCHEMA})")
    return doc["replies"]
