"""Configuration loading and command-line precedence."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ToolConfig:
    """Resolved settings for one preparation run."""

    language: str = "en-US"
    title: str | None = None
    profile: str = "auto"
    beamer_mode: str = "ltx-talk"
    table_headers: str = "first-row"
    build: bool = True
    keep_build_files: bool = False
    strict: bool = False
    image_alts: dict[str, str | None] = field(default_factory=dict)
    beamer_target_mode: str = "projector"
    stateful_graphic_default: bool = False
    stateful_tikz_default: bool = False
    local_beamer_color_themes: list[str] = field(default_factory=list)
    reachable_visual_definitions: set[str] | None = None
    tagged_theorem_environments: set[str] = field(default_factory=set)


def _normalise_images(value: Any, origin: Path) -> dict[str, str | None]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{origin}: [images] must be a table/object")
    result: dict[str, str | None] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if isinstance(raw_value, str):
            result[key] = raw_value.strip()
        elif raw_value is None:
            result[key] = None
        elif isinstance(raw_value, dict):
            if raw_value.get("decorative") is True:
                result[key] = None
            elif isinstance(raw_value.get("alt"), str):
                result[key] = raw_value["alt"].strip()
            else:
                raise ValueError(
                    f"{origin}: image {key!r} needs 'alt' text or decorative=true"
                )
        else:
            raise ValueError(
                f"{origin}: image {key!r} must be text, null, or a table"
            )
    return result


def load_config(path: Path | None) -> dict[str, Any]:
    """Load an optional TOML configuration file."""

    if path is None:
        return {}
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Could not read configuration {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level configuration must be a table")
    data["images"] = _normalise_images(data.get("images"), path)
    return data


def load_alt_map(path: Path | None) -> dict[str, str | None]:
    """Load an optional JSON image alternative map."""

    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read image alternative map {path}: {error}") from error
    if isinstance(data, dict) and "images" in data:
        data = data["images"]
    return _normalise_images(data, path)


def resolve_config(args: Any) -> ToolConfig:
    """Merge defaults, TOML, and explicit CLI values in that order."""

    raw = load_config(args.config)
    allowed = {
        "language",
        "title",
        "profile",
        "beamer_mode",
        "table_headers",
        "build",
        "keep_build_files",
        "strict",
        "images",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown configuration key(s): {', '.join(unknown)}")

    config = ToolConfig()
    for name in allowed - {"images"}:
        if name in raw:
            setattr(config, name, raw[name])
    config.image_alts.update(raw.get("images", {}))
    config.image_alts.update(load_alt_map(args.alt_map))

    for name in (
        "language",
        "title",
        "profile",
        "beamer_mode",
        "table_headers",
    ):
        value = getattr(args, name, None)
        if value is not None:
            setattr(config, name, value)
    if args.no_build:
        config.build = False
    if args.keep_build_files:
        config.keep_build_files = True
    if args.strict:
        config.strict = True

    if not isinstance(config.profile, str) or config.profile not in {
        "auto",
        "article",
        "beamer",
        "ltx-talk",
    }:
        raise ValueError(f"Unsupported profile: {config.profile}")
    if not isinstance(config.beamer_mode, str) or config.beamer_mode not in {
        "ltx-talk",
        "reject",
    }:
        raise ValueError(f"Unsupported beamer_mode: {config.beamer_mode}")
    if not isinstance(config.table_headers, str) or config.table_headers not in {
        "first-row",
        "manual",
    }:
        raise ValueError(f"Unsupported table_headers: {config.table_headers}")
    if not isinstance(config.language, str) or not re.fullmatch(
        r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", config.language.strip()
    ):
        raise ValueError("language must be a valid BCP 47-style tag, such as en-US")
    config.language = config.language.strip()
    if config.title is not None and not isinstance(config.title, str):
        raise ValueError("title must be text")
    for name in ("build", "keep_build_files", "strict"):
        if not isinstance(getattr(config, name), bool):
            raise ValueError(f"{name} must be true or false")
    return config
