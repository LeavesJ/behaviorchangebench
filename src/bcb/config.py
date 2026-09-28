"""The run config: one YAML file drives every step (configs/run.<spec>.yaml).

Sections are parsed only when a step needs them, so step 1 can run while the models under test
are still "TK", and an unset model fails the step that needs it, by name. Paths are relative to
the working directory; run from the repository root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .providers import UNSET, ModelConfig, parse_model_config
from .spec import Spec
from .util import load_yaml, sha256_file

_NAME = re.compile(r"^[a-z0-9_]+$")
_KEYS = {
    "run_id", "spec", "conversation", "examples", "prompts", "output_dir", "checkpoints", "samples",
    "system_prompt", "items", "packet_seed", "models", "graders", "generator", "prices",
}


@dataclass(frozen=True)
class RunConfig:
    path: Path
    sha256: str
    raw: dict[str, Any]

    # -- scalar settings ------------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self.raw["run_id"]

    @property
    def run_dir(self) -> Path:
        return Path(self.raw.get("output_dir", "runs")) / self.run_id

    @property
    def spec_path(self) -> Path:
        return self._path("spec")

    @property
    def conversation_path(self) -> Path:
        return self._path("conversation")

    @property
    def examples_path(self) -> Path:
        return self._path("examples")

    def prompt_path(self, name: str) -> Path:
        prompts = self.raw.get("prompts") or {}
        if name not in prompts:
            raise ConfigError(f"{self.path}: prompts.{name} is required")
        return Path(prompts[name])

    @property
    def samples(self) -> int:
        return self.raw["samples"]

    @property
    def system_prompt(self) -> str | None:
        return self.raw.get("system_prompt")

    @property
    def packet_seed(self) -> int:
        seed = self.raw.get("packet_seed")
        if not isinstance(seed, int):
            raise ConfigError(f"{self.path}: `packet_seed` (an integer) is required to build packets")
        return seed

    def _path(self, key: str) -> Path:
        value = self.raw.get(key)
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{self.path}: `{key}` is required")
        return Path(value)

    # -- spec-dependent selections ----------------------------------------------------------------

    def checkpoints(self, spec: Spec) -> tuple[str, ...]:
        value = self.raw.get("checkpoints", "default")
        if value == "default":
            return spec.default_checkpoints
        if not isinstance(value, list) or not value:
            raise ConfigError(f"{self.path}: `checkpoints` must be `default` or a list of key-moment IDs")
        unknown = [c for c in value if c not in spec.checkpoints]
        if unknown:
            raise ConfigError(f"{self.path}: unknown checkpoints {unknown}")
        order = list(spec.checkpoints)
        return tuple(sorted(dict.fromkeys(value), key=order.index))

    def selected_items(self, spec: Spec) -> frozenset[str]:
        value = self.raw.get("items", "all")
        if value == "all":
            return frozenset(spec.items)
        if not isinstance(value, list) or not value:
            raise ConfigError(f"{self.path}: `items` must be `all` or a list of item IDs")
        unknown = [i for i in value if i not in spec.items]
        if unknown:
            raise ConfigError(f"{self.path}: unknown items {unknown}")
        return frozenset(value)

    # -- models -----------------------------------------------------------------------------------

    def models(self) -> list[ModelConfig]:
        raw = self.raw.get("models")
        if not isinstance(raw, list) or not raw:
            raise ConfigError(f"{self.path}: `models` must be a non-empty list")
        models = [parse_model_config(m, f"{self.path}: models[{n}]") for n, m in enumerate(raw)]
        slots = [m.slot for m in models]
        if len(slots) != len(set(slots)):
            raise ConfigError(f"{self.path}: model slots must be unique, got {slots}")
        self._check_judge_families()
        return models

    def grader_names(self) -> list[str]:
        return list((self.raw.get("graders") or {}).keys())

    def grader(self, name: str) -> ModelConfig:
        graders = self.raw.get("graders") or {}
        if name not in graders:
            raise ConfigError(f"{self.path}: no grader named {name!r} (have {sorted(graders)})")
        config = parse_model_config(graders[name], f"{self.path}: graders.{name}", slot=name)
        self._check_judge_families()
        return config

    def generator(self) -> ModelConfig:
        raw = self.raw.get("generator")
        if raw is None:
            raise ConfigError(f"{self.path}: `generator` is required for step 1")
        config = parse_model_config(raw, f"{self.path}: generator", slot="generator")
        grader_ids = {g.get("model_id") for g in (self.raw.get("graders") or {}).values() if isinstance(g, dict)}
        if config.model_id in grader_ids:
            raise ConfigError(f"{self.path}: the generator is never a grader ({config.model_id})")
        return config

    def generator_family_under_test(self) -> bool:
        family = (self.raw.get("generator") or {}).get("family")
        return family in {m.get("family") for m in self.raw.get("models") or [] if isinstance(m, dict)}

    def _check_judge_families(self) -> None:
        """README rule 5: the judge's family is never the family of a model being judged."""
        tested = {m.get("family") for m in self.raw.get("models") or [] if isinstance(m, dict)}
        for name, g in (self.raw.get("graders") or {}).items():
            family = g.get("family") if isinstance(g, dict) else None
            if family and family != UNSET and family in tested:
                raise ConfigError(f"{self.path}: grader {name} is family {family!r}, which is under test")

    def prices(self) -> dict[str, dict[str, float]]:
        prices = self.raw.get("prices") or {}
        for model_id, p in prices.items():
            if not isinstance(p, dict) or set(p) != {"input_per_mtok", "output_per_mtok"}:
                raise ConfigError(f"{self.path}: prices.{model_id} needs exactly input_per_mtok and output_per_mtok")
        return prices


def load_config(path: str | Path) -> RunConfig:
    path = Path(path)
    raw = load_yaml(path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level is not a mapping")
    unknown = set(raw) - _KEYS
    if unknown:
        raise ConfigError(f"{path}: unknown keys {sorted(unknown)}")
    run_id = raw.get("run_id")
    if not isinstance(run_id, str) or not re.match(r"^[A-Za-z0-9._-]+$", run_id):
        raise ConfigError(f"{path}: `run_id` must be a non-empty string of letters, digits, . _ -")
    samples = raw.get("samples", 1)
    if not isinstance(samples, int) or samples < 1:
        raise ConfigError(f"{path}: `samples` must be a positive integer")
    raw["samples"] = samples
    for name in raw.get("graders") or {}:
        if not _NAME.match(name):
            raise ConfigError(f"{path}: grader name {name!r} must be lowercase letters, digits, _")
    return RunConfig(path=path, sha256=sha256_file(path), raw=raw)
