"""Render a checkpoint: the conversation cut right after a key-moment user turn.

Every model gets byte-identical input: one message list, all earlier sessions in order, and each
session's first user message prefixed with its date line and a newline (spec run.message_layout).
Variants (KM7-T, KM7-P) take the prefix through `prefix.through` and swap in their own line.
"""

from __future__ import annotations

from dataclasses import dataclass

from .conversation import Conversation
from .errors import DataError
from .spec import Checkpoint, Spec
from .util import sha256_json


@dataclass(frozen=True)
class RenderedCheckpoint:
    checkpoint: Checkpoint
    turn_ids: tuple[str, ...]
    messages: tuple[dict[str, str], ...]
    sha256: str

    @property
    def last_user_message(self) -> str:
        return self.messages[-1]["content"]


def render_checkpoint(spec: Spec, conv: Conversation, checkpoint_id: str) -> RenderedCheckpoint:
    cp = spec.checkpoints[checkpoint_id]
    if conv.order != spec.turn_order:
        raise DataError(f"{conv.path}: turn IDs differ from the spec; run `bcb conversation check`")
    order = spec.turn_order
    if cp.variant_of is None:
        ids = order[: order.index(cp.turn_id) + 1]
        texts = [conv.text(i) for i in ids]
        if texts[-1] != cp.user_line:
            raise DataError(f"{conv.path}: {cp.turn_id} differs from {cp.id}.user_line")
    else:
        ids = order[: order.index(cp.through) + 1] + (cp.turn_id,)
        texts = [conv.text(i) for i in ids[:-1]] + [cp.user_line]
    first_user = {s.turn_ids[0]: s.header for s in spec.sessions}
    messages = []
    for tid, text in zip(ids, texts, strict=True):
        role = spec.turns[tid].role
        content = f"{first_user[tid]}\n{text}" if tid in first_user else text
        messages.append({"role": role, "content": content})
    if messages[-1]["role"] != "user":
        raise DataError(f"{checkpoint_id}: a checkpoint must end on a user turn")
    return RenderedCheckpoint(cp, ids, tuple(messages), sha256_json(messages))


def transcript_text(rendered: RenderedCheckpoint) -> str:
    """The grader's view: `role: content` blocks, date lines exactly as the model saw them."""
    return "\n\n".join(f"{m['role']}: {m['content']}" for m in rendered.messages)
