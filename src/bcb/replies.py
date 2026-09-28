"""Step 2: collect each model's reply at each checkpoint, `samples` times.

One row per (checkpoint, model slot, sample) in runs/<run_id>/replies.jsonl. Rerunning resumes:
rows with status ok are kept, error rows are dropped and retried. A truncated, empty or refused
reply is recorded as an error with its reason code and never graded.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .checkpoints import RenderedCheckpoint, render_checkpoint
from .conversation import Conversation
from .errors import DataError, ReasonCode
from .providers import STOP_END, STOP_REFUSAL, STOP_TRUNCATED, Provider
from .spec import Spec
from .util import append_jsonl, read_jsonl, utc_now, word_count, write_jsonl_atomic

log = logging.getLogger("bcb.replies")

REPLY_SCHEMA = "bcb.reply/1"


def reply_key(row: dict[str, Any]) -> tuple:
    return (row["checkpoint"], row["model_slot"], row["sample"])


def run_replies(spec: Spec, conv: Conversation, run_id: str, checkpoints: tuple[str, ...], samples: int,
                system_prompt: str | None, providers: list[Provider], out_path: Path, workers: int) -> dict[str, Any]:
    rendered = {cp: render_checkpoint(spec, conv, cp) for cp in checkpoints}
    slots = {p.config.slot: p for p in providers}
    done: dict[tuple, dict[str, Any]] = {}
    if out_path.exists():
        for row in read_jsonl(out_path):
            prov = slots.get(row["model_slot"])
            if prov is not None and row["model_id"] != prov.config.model_id:
                raise DataError(f"{out_path}: slot {row['model_slot']} was {row['model_id']}, config now says "
                                f"{prov.config.model_id}. Start a new run_id instead of mixing models in one run.")
            if row["status"] == "ok":
                done[reply_key(row)] = row
        write_jsonl_atomic(out_path, _sorted(done.values(), checkpoints))
    tasks = [
        (cp, prov, s)
        for cp in checkpoints for prov in providers for s in range(1, samples + 1)
        if (cp, prov.config.slot, s) not in done
    ]

    def work(task) -> dict[str, Any]:
        cp, prov, sample = task
        return _one(spec, conv, run_id, rendered[cp], prov, sample, system_prompt)

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for fut in as_completed([pool.submit(work, t) for t in tasks]):
            row = fut.result()
            append_jsonl(out_path, row)
            tag = row["status"] if row["status"] == "ok" else row["reason_code"]
            counts[tag] = counts.get(tag, 0) + 1
            if row["status"] != "ok":
                log.error("reply_error checkpoint=%s slot=%s sample=%s reason_code=%s reason_detail=%s",
                          row["checkpoint"], row["model_slot"], row["sample"], row["reason_code"], row["reason_detail"])
    rows = {reply_key(r): r for r in read_jsonl(out_path)} if out_path.exists() else {}
    write_jsonl_atomic(out_path, _sorted(rows.values(), checkpoints))
    return {"requested": len(tasks), "by_status": counts}


def _one(spec: Spec, conv: Conversation, run_id: str, rendered: RenderedCheckpoint, prov: Provider, sample: int,
         system_prompt: str | None) -> dict[str, Any]:
    c = prov.config
    row: dict[str, Any] = {
        "schema": REPLY_SCHEMA, "run_id": run_id, "checkpoint": rendered.checkpoint.id,
        "model_slot": c.slot, "model_family": c.family, "model_id": c.model_id, "sample": sample,
        "spec_id": spec.id, "spec_version": spec.version, "conversation_sha256": conv.sha256,
        "conversation_version": str(conv.meta.get("version")), "input_sha256": rendered.sha256,
        "system_prompt": system_prompt, "request": c.describe(), "started_at": utc_now(),
    }
    try:
        comp = prov.complete(rendered.messages, system_prompt)
    except Exception as e:  # after the SDK's own retries
        row.update({"status": "error", "reason_code": str(ReasonCode.PROVIDER_ERROR),
                    "reason_detail": f"{type(e).__name__}: {e}", "reply": None, "finished_at": utc_now()})
        return row
    status, reason, detail = "ok", None, None
    if comp.stop == STOP_TRUNCATED:
        status, reason, detail = "error", ReasonCode.REPLY_TRUNCATED, f"stop {comp.raw_stop}; raise max_output_tokens"
    elif comp.stop == STOP_REFUSAL:
        status, reason, detail = "error", ReasonCode.PROVIDER_REFUSAL, comp.stop_detail
    elif comp.stop != STOP_END:
        status, reason, detail = "error", ReasonCode.PROVIDER_ERROR, f"unexpected stop {comp.raw_stop}"
    elif not comp.text.strip():
        status, reason, detail = "error", ReasonCode.REPLY_EMPTY, "no text content"
    row.update({
        "status": status, "reason_code": str(reason) if reason else None, "reason_detail": detail,
        "reply": comp.text, "reply_words": word_count(comp.text), "stop": comp.stop, "raw_stop": comp.raw_stop,
        "response_model": comp.response_model, "content_types": list(comp.content_types),
        "input_tokens": comp.input_tokens, "output_tokens": comp.output_tokens, "latency_s": comp.latency_s,
        "finished_at": utc_now(),
    })
    return row


def _sorted(rows, checkpoints: tuple[str, ...]) -> list[dict[str, Any]]:
    order = {cp: n for n, cp in enumerate(checkpoints)}
    return sorted(rows, key=lambda r: (order.get(r["checkpoint"], len(order)), r["model_slot"], r["sample"]))
