"""Hashing, file IO and text helpers shared by every step. Deterministic by construction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from .errors import DataError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical_json(obj: Any) -> str:
    """One serialization for hashing and for JSONL rows: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_yaml(path: str | Path) -> Any:
    if not Path(path).is_file():
        raise DataError(f"{path}: file not found")
    with open(path, encoding="utf-8") as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise DataError(f"{path}: invalid YAML: {e}") from e


class _BlockDumper(yaml.SafeDumper):
    """Multi-line strings as `|` blocks so conversation files stay readable and diffable."""


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_BlockDumper.add_representer(str, _str_representer)


def dump_yaml(obj: Any) -> str:
    return yaml.dump(obj, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True, width=10_000)


def write_text_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise DataError(f"{path}:{n}: invalid JSON: {e}") from e
            if not isinstance(row, dict):
                raise DataError(f"{path}:{n}: expected a JSON object per line")
            rows.append(row)
    return rows


def write_jsonl_atomic(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    write_text_atomic(path, "".join(canonical_json(r) + "\n" for r in rows))


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(canonical_json(row) + "\n")
        f.flush()


def word_count(text: str) -> int:
    return len(text.split())


_QUOTE_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_WS = re.compile(r"\s+")


def normalize_for_quote(text: str) -> str:
    """The one normalization used to check a quoted span: NFKC, straight quotes, collapsed
    whitespace. Case and punctuation are otherwise kept."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).translate(_QUOTE_FOLD)).strip()


def quote_in_text(quote: str, text: str) -> bool:
    q = normalize_for_quote(quote)
    return bool(q) and q in normalize_for_quote(text)
