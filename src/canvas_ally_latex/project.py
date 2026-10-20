"""Resolve, copy, and transform a LaTeX project without touching its source."""

from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .config import ToolConfig
from .model import Finding, PrepareResult
from .transform import (
    CANVASALLY_STYLE,
    LTX_TALK_CONTENT_SHIM,
    LTX_TALK_SHIM,
    PlaceholderAllocator,
    _annotation_mode,
    _annotation_structure_tag,
    _drawing_options,
    _drawing_suppresses_children,
    _has_stateful_native_default,
    _picture_drawing_matches,
    _preprocess_beamer_content,
    _tikz_drawing_matches,
    detect_document_class,
    group_content,
    iter_commands,
    iter_graphic_commands,
    line_number,
    mask_tex,
    reachable_visual_definitions,
    reduce_literal_conditionals,
    split_top_level,
    tagged_theorem_environment_names,
    transform_tex_file,
)


@dataclass(slots=True)
class ProjectPaths:
    source: Path
    main: Path
    output: Path

    @property
    def main_relative(self) -> Path:
        return self.main.relative_to(self.source)


def _contains_document(text: str) -> bool:
    try:
        detect_document_class(text)
    except ValueError:
        return False
    return any(
        group_content(text, command.mandatory) == "document"
        for command in iter_commands(text, "begin")
    )


def _resolve_main(source: Path, requested: Path | None) -> Path:
    if requested is not None:
        candidate = requested if requested.is_absolute() else source / requested
        candidate = candidate.resolve()
        if not candidate.is_relative_to(source):
            raise ValueError(f"Main file must be inside the project directory: {candidate}")
        if not candidate.is_file():
            raise ValueError(f"Main TeX file does not exist: {candidate}")
        if candidate.suffix.lower() != ".tex":
            raise ValueError(f"Main file must have a .tex extension: {candidate}")
        return candidate

    candidates: list[Path] = []
    for path in sorted(source.rglob("*.tex")):
        if any(part.startswith(".") for part in path.relative_to(source).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _contains_document(text):
            candidates.append(path.resolve())
    if not candidates:
        raise ValueError(
            f"No main TeX file found under {source}; pass --main relative/path.tex."
        )
    if len(candidates) > 1:
        choices = "\n  ".join(str(path.relative_to(source)) for path in candidates)
        raise ValueError(
            "More than one main TeX file was found; choose one with --main:\n  " + choices
        )
    return candidates[0]


def resolve_project(
    input_path: Path,
    *,
    main: Path | None = None,
    output_dir: Path | None = None,
) -> ProjectPaths:
    """Resolve source root, main file, and safe default destination."""

    target = input_path.expanduser().resolve()
    if target.is_file():
        if target.suffix.lower() != ".tex":
            raise ValueError(f"Input file must have a .tex extension: {target}")
        if main is not None:
            raise ValueError("Do not combine a .tex input path with --main")
        source = target.parent
        selected_main = target
        default_output = source.parent / f"{target.stem}_ally"
    elif target.is_dir():
        source = target
        selected_main = _resolve_main(source, main)
        default_output = source.parent / f"{source.name}_ally"
    else:
        raise ValueError(f"Input path does not exist: {target}")

    requested_output = output_dir.expanduser() if output_dir else default_output
    lexical_output = Path(os.path.abspath(requested_output))
    # Canonicalize parent aliases (for example /var -> /private/var on macOS)
    # without following a final-component symlink that --force should unlink.
    output = lexical_output.parent.resolve() / lexical_output.name
    effective_output = output.resolve(strict=False)
    if effective_output == source:
        raise ValueError("Output directory cannot be the source project")
    if effective_output.is_relative_to(source):
        raise ValueError("Output directory cannot be nested inside the source project")
    if source.is_relative_to(effective_output):
        raise ValueError("Output directory cannot contain the source project")
    if output.parent == output or len(output.parts) < 3:
        raise ValueError(f"Refusing unsafe output directory: {output}")
    return ProjectPaths(source=source, main=selected_main, output=output)


def copy_project(paths: ProjectPaths, *, force: bool = False) -> None:
    """Copy the complete source tree, preserving dependencies and symlinks."""

    if paths.output.exists() or paths.output.is_symlink():
        if not force:
            raise FileExistsError(
                f"Output already exists: {paths.output}. Pass --force to replace it."
            )
        if paths.output.is_symlink() or paths.output.is_file():
            paths.output.unlink()
        else:
            shutil.rmtree(paths.output)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".DS_Store"}
        }

    shutil.copytree(paths.source, paths.output, symlinks=True, ignore=ignore)
    _make_copied_tree_owner_writable(paths.output)


def _make_copied_tree_owner_writable(root: Path) -> None:
    """Make a generated copy usable by its owner without following symlinks."""

    def make_writable(path: Path) -> None:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            return
        if stat.S_ISDIR(metadata.st_mode):
            required = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        elif stat.S_ISREG(metadata.st_mode):
            required = stat.S_IRUSR | stat.S_IWUSR
        else:
            return
        if metadata.st_mode & required == required:
            return
        path.chmod(
            stat.S_IMODE(metadata.st_mode) | required,
            follow_symlinks=False,
        )

    make_writable(root)
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        make_writable(parent)
        for name in directory_names:
            make_writable(parent / name)
        for name in file_names:
            make_writable(parent / name)


def _dependency_closure(
    main: Path,
    root: Path,
    *,
    strict: bool,
    beamer_target_mode: str | None = None,
) -> tuple[list[Path], list[Finding]]:
    """Resolve brace-form input/include/subfile dependencies inside the copy."""

    queue = [main]
    ordered: list[Path] = []
    seen: set[Path] = set()
    findings: list[Finding] = []
    include_only: set[str] | None = None
    main_source = main.read_text(encoding="utf-8")
    active_offsets = [
        offset
        for offset in (
            _active_document_end(main_source, beamer_target_mode),
            _active_endinput(main_source),
        )
        if offset is not None
    ]
    if active_offsets:
        main_source = main_source[: min(active_offsets)]
    if beamer_target_mode is not None:
        main_source, _ = _preprocess_beamer_content(
            main_source,
            main.relative_to(root),
            beamer_target_mode,
        )
    main_for_includes = _mask_definition_regions(
        mask_tex(_reduce_static_conditionals(main_source))
    )
    document_begin = next(
        (
            command
            for command in iter_commands(main_for_includes, "begin")
            if group_content(main_for_includes, command.mandatory) == "document"
        ),
        None,
    )
    if document_begin is not None:
        main_for_includes = main_for_includes[: document_begin.start]
    includeonly_commands = iter_commands(main_for_includes, "includeonly")
    if includeonly_commands:
        raw_includeonly = (
            group_content(main_for_includes, includeonly_commands[-1].mandatory) or ""
        )
        if any(character in raw_includeonly for character in ("\\", "#", "{", "}")):
            findings.append(
                Finding(
                    "dynamic-includeonly",
                    "Could not statically resolve \\includeonly; excluded files may require manual review.",
                    "error" if strict else "warning",
                    str(main.relative_to(root)),
                    line_number(main_for_includes, includeonly_commands[-1].start),
                )
            )
        else:
            include_only = {
                (item[:-4] if item.lower().endswith(".tex") else item)
                for item in (part.strip() for part in raw_includeonly.split(","))
                if item
            }
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"Could not read TeX dependency {path}: {error}") from error
        relative = path.relative_to(root)
        scan_text = text
        active_offsets: list[int] = []
        if path == main:
            active_end = _active_document_end(text, beamer_target_mode)
            if active_end is not None:
                active_offsets.append(active_end)
        endinput = _active_endinput(text)
        if endinput is not None:
            active_offsets.append(endinput)
        if active_offsets:
            scan_text = text[: min(active_offsets)]
        if beamer_target_mode is not None:
            scan_text, _ = _preprocess_beamer_content(
                scan_text,
                relative,
                beamer_target_mode,
            )
        scan_text = _reduce_static_conditionals(scan_text)
        masked = mask_tex(scan_text)
        for command_name in ("input", "include", "subfile"):
            for command in iter_commands(scan_text, command_name):
                raw = (group_content(scan_text, command.mandatory) or "").strip()
                if not raw and command.mandatory is None and command_name == "input":
                    cursor = command.end
                    if cursor < len(masked) and masked[cursor] == '"':
                        closing_quote = masked.find('"', cursor + 1)
                        if closing_quote >= 0:
                            raw = scan_text[cursor + 1 : closing_quote].strip()
                    else:
                        filename = re.match(r"[^\s%]+", masked[cursor:])
                        if filename:
                            raw = scan_text[cursor : cursor + len(filename.group(0))].strip()
                if not raw:
                    continue
                if command_name == "include" and include_only is not None:
                    include_name = raw[:-4] if raw.lower().endswith(".tex") else raw
                    if include_name not in include_only:
                        continue
                if any(character in raw for character in ("\\", "#", "{", "}")):
                    findings.append(
                        Finding(
                            "dynamic-tex-dependency",
                            f"Could not statically resolve \\{command_name}{{{raw}}}; inspect this dependency manually.",
                            "error" if strict else "warning",
                            str(relative),
                            line_number(text, command.start),
                        )
                    )
                    continue
                dependency = Path(raw)
                if not dependency.suffix:
                    dependency = dependency.with_suffix(".tex")
                # LuaLaTeX runs from the selected main file's directory; plain
                # \input does not change cwd to the including file's folder.
                candidates = [
                    main.parent / dependency,
                    root / dependency,
                    path.parent / dependency,
                ]
                selected: Path | None = None
                for candidate in candidates:
                    normalized = Path(os.path.abspath(candidate))
                    if normalized.is_relative_to(root) and normalized.is_file():
                        selected = normalized
                        break
                if selected is not None:
                    if selected not in seen:
                        queue.append(selected)
                    continue
                findings.append(
                    Finding(
                        "unresolved-tex-dependency",
                        f"Could not find local dependency from \\{command_name}{{{raw}}}; TeX may resolve it externally.",
                        "error" if strict else "warning",
                        str(relative),
                        line_number(text, command.start),
                    )
                )
    return ordered, findings


def _materialize_parent_symlinks(path: Path, root: Path) -> None:
    """Replace reachable symlinked directories before copied TeX is written."""

    cursor = root
    for part in path.relative_to(root).parts[:-1]:
        cursor = cursor / part
        if not cursor.is_symlink():
            continue
        target = cursor.resolve()
        if not target.is_dir():
            raise ValueError(f"TeX dependency parent is not a directory: {cursor}")
        if target == root or root.is_relative_to(target):
            raise ValueError(f"Refusing recursive project-directory symlink: {cursor} -> {target}")
        cursor.unlink()
        shutil.copytree(target, cursor, symlinks=True)
        _make_copied_tree_owner_writable(cursor)


def _mask_tikz_regions(masked: str) -> str:
    chars = list(masked)
    drawings = _tikz_drawing_matches(masked) + _picture_drawing_matches(masked)
    for drawing in drawings:
        if _drawing_suppresses_children(masked, drawing):
            _blank_project_range(chars, drawing.start, drawing.end)
    return "".join(chars)


def _mask_graphic_commands(masked: str) -> str:
    chars = list(masked)
    for command in iter_graphic_commands(masked):
        _blank_project_range(chars, command.start, command.end)
    return "".join(chars)


def _mask_first_argument_contents(masked: str, command_names: tuple[str, ...]) -> str:
    """Blank non-rendered destinations/literals while retaining link bodies."""

    chars = list(masked)
    for name in command_names:
        for command in iter_commands(masked, name):
            if command.mandatory is not None:
                _blank_project_range(
                    chars,
                    command.mandatory[0] + 1,
                    command.mandatory[1] - 1,
                )
    return "".join(chars)


def _mask_command_content(
    masked: str,
    command_name: str,
    *,
    required_overlay: str | None = None,
) -> str:
    """Blank complete command invocations that never reach the static PDF."""

    chars = list(masked)
    pattern = re.compile(r"\\" + re.escape(command_name) + r"(?![A-Za-z@])")
    for match in pattern.finditer(masked):
        cursor = _skip_space_for_project(masked, match.end())
        overlay = None
        # Beamer notes may have an overlay specification and an optional key.
        for opener, closer in (("<", ">"), ("[", "]")):
            if cursor < len(masked) and masked[cursor] == opener:
                end = _matching_group_for_project(masked, cursor, opener, closer)
                if end is None:
                    break
                if opener == "<":
                    overlay = masked[cursor + 1 : end - 1].strip()
                cursor = _skip_space_for_project(masked, end)
        if required_overlay is not None and overlay != required_overlay:
            continue
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        end = _matching_group_for_project(masked, cursor, "{", "}")
        if end is None:
            continue
        for index in range(match.start(), end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def _mask_disabled_frame_regions(masked: str) -> str:
    """Suppress frames whose final tagged-slide content cannot be guaranteed."""

    chars = list(masked)
    token = re.compile(r"\\(begin|end)\s*\{(frame\*?)\}")
    stack: list[tuple[str, re.Match[str]]] = []
    for match in token.finditer(masked):
        if match.group(1) == "begin":
            stack.append((match.group(2), match))
            continue
        if not stack:
            continue
        environment, opening = stack.pop()
        if environment != match.group(2):
            continue

        cursor = _skip_space_for_project(masked, opening.end())
        overlay = "all"
        if cursor < len(masked) and masked[cursor] == "<":
            overlay_end = _matching_group_for_project(masked, cursor, "<", ">")
            if overlay_end is None or overlay_end > match.start():
                _blank_project_range(chars, opening.start(), match.end())
                continue
            overlay = re.sub(
                r"\s+",
                "",
                masked[cursor + 1 : overlay_end - 1],
            ).lower()
            cursor = _skip_space_for_project(masked, overlay_end)

        default_final_tagging = True
        if cursor < len(masked) and masked[cursor] == "[":
            options_end = _matching_group_for_project(masked, cursor, "[", "]")
            if options_end is None or options_end > match.start():
                _blank_project_range(chars, opening.start(), match.end())
                continue
            options = masked[cursor + 1 : options_end - 1]
            for option in split_top_level(options):
                key, separator, value = option.partition("=")
                if key.strip() == "tag-slides" and (
                    not separator or re.sub(r"\s+", "", value) != "n"
                ):
                    default_final_tagging = False
                    break

        selected_on_final = overlay in {"", "all"} or (
            _literal_overlay_guaranteed_on_final(overlay)
        )
        if not default_final_tagging or not selected_on_final:
            _blank_project_range(chars, opening.start(), match.end())
    return "".join(chars)


def _skip_space_for_project(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _matching_group_for_project(
    text: str,
    start: int,
    opener: str,
    closer: str,
) -> int | None:
    if start >= len(text) or text[start] != opener:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opener and not _escaped_for_project(text, index):
            depth += 1
        elif text[index] == closer and not _escaped_for_project(text, index):
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _blank_project_range(chars: list[str], start: int, end: int) -> None:
    for index in range(start, min(end, len(chars))):
        if chars[index] not in "\r\n":
            chars[index] = " "


def _control_or_group_end(text: str, cursor: int) -> int | None:
    cursor = _skip_space_for_project(text, cursor)
    if cursor < len(text) and text[cursor] == "{":
        return _matching_group_for_project(text, cursor, "{", "}")
    match = re.match(r"\\(?:[A-Za-z@]+|.)", text[cursor:])
    return cursor + len(match.group(0)) if match else None


def _mask_definition_regions(masked: str) -> str:
    """Blank common macro/environment definitions before rendered-tag estimates."""

    chars = list(masked)
    command_definitions = (
        "newcommand",
        "renewcommand",
        "providecommand",
        "DeclareRobustCommand",
    )
    for name in command_definitions:
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            cursor = _skip_space_for_project(masked, match.end())
            if masked.startswith("<>", cursor):
                cursor = _skip_space_for_project(masked, cursor + 2)
            if cursor < len(masked) and masked[cursor] == "*":
                cursor = _skip_space_for_project(masked, cursor + 1)
            cursor = _control_or_group_end(masked, cursor) or -1
            if cursor < 0:
                continue
            for _ in range(2):
                cursor = _skip_space_for_project(masked, cursor)
                if cursor < len(masked) and masked[cursor] == "[":
                    end = _matching_group_for_project(masked, cursor, "[", "]")
                    if end is None:
                        break
                    cursor = end
            cursor = _skip_space_for_project(masked, cursor)
            end = _matching_group_for_project(masked, cursor, "{", "}")
            if end is not None:
                _blank_project_range(chars, match.start(), end)

    for name in ("newenvironment", "renewenvironment"):
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            cursor = _skip_space_for_project(masked, match.end())
            cursor = _matching_group_for_project(masked, cursor, "{", "}") or -1
            if cursor < 0:
                continue
            for _ in range(2):
                cursor = _skip_space_for_project(masked, cursor)
                if cursor < len(masked) and masked[cursor] == "[":
                    end = _matching_group_for_project(masked, cursor, "[", "]")
                    if end is None:
                        break
                    cursor = end
            valid = True
            for _ in range(2):
                cursor = _skip_space_for_project(masked, cursor)
                end = _matching_group_for_project(masked, cursor, "{", "}")
                if end is None:
                    valid = False
                    break
                cursor = end
            if valid:
                _blank_project_range(chars, match.start(), cursor)

    xparse_commands = (
        "NewDocumentCommand",
        "RenewDocumentCommand",
        "ProvideDocumentCommand",
        "DeclareDocumentCommand",
        "NewExpandableDocumentCommand",
    )
    for name in xparse_commands:
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            cursor = _control_or_group_end(masked, match.end()) or -1
            if cursor < 0:
                continue
            valid = True
            for _ in range(2):
                cursor = _skip_space_for_project(masked, cursor)
                end = _matching_group_for_project(masked, cursor, "{", "}")
                if end is None:
                    valid = False
                    break
                cursor = end
            if valid:
                _blank_project_range(chars, match.start(), cursor)

    for name in ("NewDocumentEnvironment", "RenewDocumentEnvironment", "ProvideDocumentEnvironment"):
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            cursor = match.end()
            valid = True
            for _ in range(4):
                cursor = _skip_space_for_project(masked, cursor)
                end = _matching_group_for_project(masked, cursor, "{", "}")
                if end is None:
                    valid = False
                    break
                cursor = end
            if valid:
                _blank_project_range(chars, match.start(), cursor)

    for match in re.finditer(r"\\(?:def|gdef|edef|xdef)(?![A-Za-z@])", masked):
        body_start = masked.find("{", match.end())
        if body_start < 0:
            continue
        end = _matching_group_for_project(masked, body_start, "{", "}")
        if end is not None:
            _blank_project_range(chars, match.start(), end)
    return "".join(chars)


def _reduce_static_conditionals(text: str) -> str:
    token_mask = _mask_definition_regions(mask_tex(text))
    chars = list(token_mask)
    assignment_pattern = re.compile(
        r"(?:\\global\s*)?\\(?:let|futurelet)(?![A-Za-z@])"
    )
    for match in assignment_pattern.finditer(token_mask):
        cursor = _skip_space_for_project(token_mask, match.end())
        first = re.match(r"\\(?:[A-Za-z@]+|.)|.", token_mask[cursor:])
        if first is None:
            continue
        cursor += len(first.group(0))
        cursor = _skip_space_for_project(token_mask, cursor)
        if cursor < len(token_mask) and token_mask[cursor] == "=":
            cursor = _skip_space_for_project(token_mask, cursor + 1)
        second = re.match(r"\\(?:[A-Za-z@]+|.)|.", token_mask[cursor:])
        if second is None:
            continue
        _blank_project_range(chars, match.start(), cursor + len(second.group(0)))
    token_mask = "".join(chars)
    return reduce_literal_conditionals(text, token_mask=token_mask)


def _active_document_end(
    text: str,
    beamer_target_mode: str | None = None,
) -> int | None:
    """Return the end offset of the first active ``\\end{document}``."""

    # Comments, literal environments, statically false branches, and common
    # definitions cannot supply the active end marker.
    static_text = _reduce_static_conditionals(text)
    masked = _mask_definition_regions(mask_tex(static_text))
    document_begin = next(
        (
            command
            for command in iter_commands(masked, "begin")
            if group_content(masked, command.mandatory) == "document"
        ),
        None,
    )
    if document_begin is None:
        return None
    candidates = [
        command
        for command in iter_commands(masked, "end")
        if command.start >= document_begin.end
        and group_content(masked, command.mandatory) == "document"
    ]
    if not candidates or beamer_target_mode is None:
        return candidates[0].end if candidates else None

    # Determine which candidate survives Beamer's static-mode filtering while
    # retaining original offsets.  Markers inside notes, hooks, article-only
    # branches, and zero overlays are removed by the same preprocessor used by
    # the actual transformation.
    replacements: list[tuple[int, int, str]] = []
    markers: list[str] = []
    for index, command in enumerate(candidates, start=1):
        marker = f"CanvasAllyEndCandidate{index:06d}"
        markers.append(marker)
        replacements.append(
            (
                command.start,
                command.end,
                text[command.start : command.end] + marker,
            )
        )
    marked = text
    for start, end, replacement in sorted(replacements, reverse=True):
        marked = marked[:start] + replacement + marked[end:]
    static_text, _ = _preprocess_beamer_content(
        marked,
        Path("main.tex"),
        beamer_target_mode,
    )
    for command, marker in zip(candidates, markers):
        if marker in static_text:
            return command.end
    return None


def _active_endinput(text: str) -> int | None:
    r"""Return the first active ``\endinput`` offset, if statically visible."""

    static_text = _reduce_static_conditionals(text)
    masked = _mask_definition_regions(mask_tex(static_text))
    match = re.search(r"\\endinput(?![A-Za-z@])", masked)
    return match.end() if match else None


def _mandatory_argument_bounds(
    text: str,
    cursor: int,
) -> tuple[int, int, int, int] | None:
    """Return an argument and its content bounds, including single-token args."""

    start = _skip_space_for_project(text, cursor)
    if start >= len(text):
        return None
    if text[start] == "{":
        end = _matching_group_for_project(text, start, "{", "}")
        if end is None:
            return None
        return start, end, start + 1, end - 1
    if text[start] == "\\":
        token = re.match(r"\\(?:[A-Za-z@]+|.)", text[start:])
        if token is None:
            return None
        end = start + len(token.group(0))
        return start, end, start, end
    return start, start + 1, start, start + 1


def _reduce_overlay_command_branches(masked: str) -> str:
    """Retain branches known to be tagged in the static ltx-talk slide copy.

    Presentation preprocessing maps a definitely selected Beamer mode to
    ``<all>`` and a definitely omitted one to ``<0>``. An omitted overlay
    argument also defaults to ``all`` in ltx-talk. By default, ltx-talk tags
    only a frame's final generated slide. Exact, bounded, and relative-only
    selections therefore cannot establish which branch is tagged; they are
    conservatively excluded. Open-ended ranges are active on the final slide.
    Visible/uncover content is executed even when hidden, so every invocation
    that remains after static preprocessing still contributes tags there.
    """

    # Values are mandatory argument counts.
    commands = {
        "only": 1,
        "mode": 1,
        "visible": 1,
        "uncover": 1,
        "alt": 2,
        "temporal": 3,
    }
    chars = list(masked)
    for name, argument_count in commands.items():
        pattern = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
        for match in pattern.finditer(masked):
            cursor = _skip_space_for_project(masked, match.end())
            overlay = "all"
            if cursor < len(masked) and masked[cursor] == "<":
                overlay_end = _matching_group_for_project(masked, cursor, "<", ">")
                if overlay_end is None:
                    # An incomplete overlay specification cannot establish a
                    # reliable rendered-object minimum.
                    _blank_project_range(chars, match.start(), match.end())
                    continue
                overlay = re.sub(r"\s+", "", masked[cursor + 1 : overlay_end - 1]).lower()
                cursor = overlay_end

            arguments: list[tuple[int, int, int, int]] = []
            for _ in range(argument_count):
                argument = _mandatory_argument_bounds(masked, cursor)
                if argument is None:
                    break
                arguments.append(argument)
                cursor = argument[1]
            if len(arguments) != argument_count:
                # At least suppress the wrapper token. Any fully parsed
                # arguments are suppressed too because their selection is not
                # knowable from a malformed invocation.
                parsed_end = arguments[-1][1] if arguments else match.end()
                _blank_project_range(chars, match.start(), parsed_end)
                continue

            invocation_end = arguments[-1][1]
            selected_argument: int | None = None
            all_slides = overlay in {"", "all"}
            if name == "mode":
                if all_slides:
                    selected_argument = 0
            elif name in {"visible", "uncover"}:
                selected_argument = 0
            elif name == "only":
                if all_slides or _literal_overlay_guaranteed_on_final(overlay):
                    selected_argument = 0
            elif name == "alt":
                if _literal_overlay_is_empty(overlay):
                    selected_argument = 1
                elif all_slides or _literal_overlay_guaranteed_on_final(overlay):
                    selected_argument = 0
            elif name == "temporal":
                if _literal_overlay_is_empty(overlay):
                    selected_argument = 0
                elif all_slides or _literal_overlay_guaranteed_on_final(overlay):
                    selected_argument = 1

            if selected_argument is None:
                _blank_project_range(chars, match.start(), invocation_end)
                continue

            _, _, content_start, content_end = arguments[selected_argument]
            # Only blank syntax and unselected branches. Never restore text:
            # a selected nested command inside an unselected outer wrapper
            # must remain blank regardless of traversal order.
            _blank_project_range(chars, match.start(), content_start)
            _blank_project_range(chars, content_end, invocation_end)
    return "".join(chars)


def _literal_overlay_guaranteed_on_final(overlay: str) -> bool:
    """Whether a recognized overlay must be active on ltx-talk's final slide."""

    if overlay == "*":
        return True
    endpoint = r"(?:[0-9]+|[+.](?:\([+-]?[0-9]+\))?)"
    return any(
        re.fullmatch(endpoint + r"-", item) is not None
        for item in overlay.split(",")
    )


def _literal_overlay_is_empty(overlay: str) -> bool:
    """Return whether a complete numeric slide list has no positive slide."""

    if not overlay:
        return False
    for item in overlay.split(","):
        if re.fullmatch(r"[0-9]+", item):
            if int(item) > 0:
                return False
            continue
        match = re.fullmatch(r"([0-9]*)-([0-9]*)", item)
        if match is None or not any(match.groups()):
            return False
        lower = int(match.group(1) or 0)
        upper = int(match.group(2)) if match.group(2) else None
        if upper is None or max(1, lower) <= upper:
            return False
    return True


def _mask_conditional_environments(masked: str) -> str:
    """Keep remaining overlay-environment bodies, which execute while hidden."""

    chars = list(masked)
    for environment in ("onlyenv", "visibleenv", "uncoverenv"):
        token = re.compile(r"\\(begin|end)\s*\{" + environment + r"\}")
        stack: list[re.Match[str]] = []
        for match in token.finditer(masked):
            if match.group(1) == "begin":
                stack.append(match)
            elif stack:
                opening = stack.pop()
                cursor = _skip_space_for_project(masked, opening.end())
                overlay = "all"
                opening_end = opening.end()
                if cursor < len(masked) and masked[cursor] == "<":
                    overlay_end = _matching_group_for_project(
                        masked,
                        cursor,
                        "<",
                        ">",
                    )
                    if overlay_end is None or overlay_end > match.start():
                        _blank_project_range(chars, opening.start(), match.end())
                        continue
                    overlay = re.sub(
                        r"\s+",
                        "",
                        masked[cursor + 1 : overlay_end - 1],
                    ).lower()
                    opening_end = overlay_end
                # ltx-talk executes all three environment bodies on the final
                # tagged slide. A false onlyenv uses a discarded box, but its
                # structure tags are still emitted. Static-empty and excluded
                # mode environments were removed during preprocessing.
                _blank_project_range(chars, opening.start(), opening_end)
                _blank_project_range(chars, match.start(), match.end())
    return "".join(chars)


def _math_candidate_count(source: str) -> int:
    environment_name = (
        r"math|displaymath|equation\*?|align\*?|alignat\*?|gather\*?|"
        r"multline\*?|flalign\*?|eqnarray\*?"
    )
    environment_token = re.compile(
        r"\\(begin|end)\s*\{(" + environment_name + r")\}"
    )
    environment_matches = list(environment_token.finditer(source))
    environments = sum(
        match.group(1) == "begin" for match in environment_matches
    )
    delimiters = sum(
        not _escaped_for_project(source, match.start())
        for match in re.finditer(r"\\(?:\(|\[)", source)
    )

    # A dollar pair used inside an already-active math container (most often
    # inside ``\text{...}``) is part of that container's one Formula object;
    # it does not create another Formula in the tagged PDF.  Blank only fully
    # matched containers before scanning dollars so malformed TeX continues
    # to fail conservatively rather than lowering the source minimum.
    dollar_chars = list(source)
    math_container_ranges: list[tuple[int, int]] = []
    environment_stack: list[tuple[str, int]] = []
    for match in environment_matches:
        name = match.group(2)
        if match.group(1) == "begin":
            environment_stack.append((name, match.start()))
            continue
        opening_index = next(
            (
                index
                for index in range(len(environment_stack) - 1, -1, -1)
                if environment_stack[index][0] == name
            ),
            None,
        )
        if opening_index is None:
            continue
        _, opening = environment_stack.pop(opening_index)
        _blank_project_range(dollar_chars, opening, match.end())
        math_container_ranges.append((opening, match.end()))

    delimiter_stack: list[tuple[str, int]] = []
    closing_for = {"(": ")", "[": "]"}
    for match in re.finditer(r"\\([()\[\]])", source):
        if _escaped_for_project(source, match.start()):
            continue
        symbol = match.group(1)
        if symbol in closing_for:
            delimiter_stack.append((symbol, match.start()))
            continue
        opening_index = next(
            (
                index
                for index in range(len(delimiter_stack) - 1, -1, -1)
                if closing_for[delimiter_stack[index][0]] == symbol
            ),
            None,
        )
        if opening_index is None:
            continue
        _, opening = delimiter_stack.pop(opening_index)
        _blank_project_range(dollar_chars, opening, match.end())
        math_container_ranges.append((opening, match.end()))

    ensuremath = 0
    for match in re.finditer(r"\\ensuremath(?![A-Za-z@])", source):
        if _escaped_for_project(source, match.start()):
            continue
        if any(
            start <= match.start() < stop
            for start, stop in math_container_ranges
        ):
            continue
        cursor = _skip_space_for_project(source, match.end())
        end = _matching_group_for_project(source, cursor, "{", "}")
        if end is None:
            # Preserve the old conservative behavior for malformed input; it
            # will fail compilation, and its unknown extent must not suppress
            # any following math candidates.
            ensuremath += 1
            continue
        ensuremath += 1
        _blank_project_range(dollar_chars, match.start(), end)
        math_container_ranges.append((match.start(), end))

    dollar_source = "".join(dollar_chars)
    single_dollars = 0
    double_dollars = 0
    index = 0
    while index < len(dollar_source):
        if dollar_source[index] != "$" or _escaped_for_project(dollar_source, index):
            index += 1
            continue
        if index + 1 < len(dollar_source) and dollar_source[index + 1] == "$":
            double_dollars += 1
            index += 2
        else:
            single_dollars += 1
            index += 1
    return environments + delimiters + ensuremath + single_dollars // 2 + double_dollars // 2


def _ambiguous_final_overlay_offset(masked: str) -> int | None:
    """Locate an only-like overlay whose final tagged branch is uncertain."""

    def ambiguous(overlay: str) -> bool:
        return not (
            overlay in {"", "all"}
            or _literal_overlay_is_empty(overlay)
            or _literal_overlay_guaranteed_on_final(overlay)
        )

    offsets: list[int] = []
    for name in ("only", "alt", "temporal", "includegraphics"):
        pattern = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
        for match in pattern.finditer(masked):
            cursor = match.end()
            if name == "includegraphics" and cursor < len(masked) and masked[cursor] == "*":
                cursor += 1
            cursor = _skip_space_for_project(masked, cursor)
            if cursor >= len(masked) or masked[cursor] != "<":
                continue
            overlay_end = _matching_group_for_project(masked, cursor, "<", ">")
            if overlay_end is None:
                offsets.append(match.start())
                continue
            overlay = re.sub(
                r"\s+",
                "",
                masked[cursor + 1 : overlay_end - 1],
            ).lower()
            if ambiguous(overlay):
                offsets.append(match.start())

    for opening in re.finditer(r"\\begin\s*\{frame\*?\}", masked):
        cursor = _skip_space_for_project(masked, opening.end())
        if cursor < len(masked) and masked[cursor] == "<":
            overlay_end = _matching_group_for_project(masked, cursor, "<", ">")
            if overlay_end is None:
                offsets.append(opening.start())
                continue
            overlay = re.sub(
                r"\s+",
                "",
                masked[cursor + 1 : overlay_end - 1],
            ).lower()
            if ambiguous(overlay):
                offsets.append(opening.start())
            cursor = _skip_space_for_project(masked, overlay_end)
        if cursor >= len(masked) or masked[cursor] != "[":
            continue
        options_end = _matching_group_for_project(masked, cursor, "[", "]")
        if options_end is None:
            offsets.append(opening.start())
            continue
        options = masked[cursor + 1 : options_end - 1]
        for option in split_top_level(options):
            key, separator, value = option.partition("=")
            if key.strip() == "tag-slides" and (
                not separator or re.sub(r"\s+", "", value) != "n"
            ):
                offsets.append(opening.start())
                break
    return min(offsets) if offsets else None


def _source_expectations(
    tex_files: list[Path],
    *,
    review_findings: list[Finding] | None = None,
    root: Path | None = None,
    strict: bool = False,
) -> dict[str, bool | int]:
    """Record conservative lower bounds for tags expected from reachable source."""

    expected = {
        "formulas": False,
        "figures": False,
        "tables": False,
        "headings": False,
        "lists": False,
        "formula_minimum": 0,
        "figure_minimum": 0,
        "table_minimum": 0,
        "list_minimum": 0,
    }
    for file_index, path in enumerate(tex_files):
        text = path.read_text(encoding="utf-8")
        active_offsets: list[int] = []
        if file_index == 0:
            active_end = _active_document_end(text)
            if active_end is not None:
                active_offsets.append(active_end)
        endinput = _active_endinput(text)
        if endinput is not None:
            active_offsets.append(endinput)
        if active_offsets:
            text = text[: min(active_offsets)]
        masked = mask_tex(text)

        if file_index == 0:
            document_begin = next(
                (
                    command
                    for command in iter_commands(text, "begin")
                    if group_content(text, command.mandatory) == "document"
                ),
                None,
            )
            if document_begin is not None:
                masked = masked[document_begin.end :]

        ambiguity_source = _mask_definition_regions(masked)
        ambiguity_offset = _ambiguous_final_overlay_offset(ambiguity_source)
        if review_findings is not None and ambiguity_offset is not None:
            relative = path.relative_to(root) if root is not None else path
            review_findings.append(
                Finding(
                    "ambiguous-overlay-tag-minimum",
                    "One or more only-like overlays cannot establish which content "
                    "is tagged on ltx-talk's final slide, or the frame overrides "
                    "tag-slides; those contents were conservatively excluded from "
                    "source minima. Inspect the generated PDF tag tree manually.",
                    "error" if strict else "warning",
                    str(relative),
                )
            )

        # The ltx-talk compatibility layer deliberately omits Beamer speaker
        # notes from the static slide PDF, so their contents cannot establish
        # expected tag minima.
        masked = _mask_command_content(masked, "note")
        masked = _mask_definition_regions(masked)
        masked = _reduce_overlay_command_branches(masked)
        masked = _mask_conditional_environments(masked)
        masked = _mask_disabled_frame_regions(masked)

        # Math inside an alt-tagged TikZ diagram is part of the diagram's
        # alternative, not necessarily a separate Formula in the PDF tree.
        content_source = _mask_tikz_regions(masked)
        math_source = _mask_graphic_commands(content_source)
        math_source = _mask_first_argument_contents(
            math_source,
            ("url", "path", "nolinkurl", "href", "hyperlink", "hypertarget"),
        )
        expected["formula_minimum"] += _math_candidate_count(math_source)

        for command in iter_graphic_commands(content_source):
            if command.overlay is not None:
                overlay = re.sub(
                    r"\s+",
                    "",
                    group_content(content_source, command.overlay) or "",
                ).lower()
                if overlay not in {"", "all"} and not (
                    _literal_overlay_guaranteed_on_final(overlay)
                ):
                    continue
            options = ",".join(
                group_content(content_source, group) or ""
                for group in command.optionals
            )
            mode, _ = _annotation_mode(options, kind="graphic")
            if mode not in {"artifact", "actualtext", "text", "off"}:
                structure_tag = _annotation_structure_tag(options, kind="graphic")
                if structure_tag and structure_tag.lower() != "figure":
                    if structure_tag.lower() == "formula":
                        expected["formula_minimum"] += 1
                else:
                    expected["figure_minimum"] += 1
        drawings = _tikz_drawing_matches(masked) + _picture_drawing_matches(masked)
        for drawing in drawings:
            options = _drawing_options(masked, drawing)
            mode, _ = _annotation_mode(options, kind=drawing.kind)
            if mode not in {"artifact", "actualtext", "text", "off"}:
                structure_tag = _annotation_structure_tag(options, kind=drawing.kind)
                if structure_tag and structure_tag.lower() != "figure":
                    if structure_tag.lower() == "formula":
                        expected["formula_minimum"] += 1
                else:
                    expected["figure_minimum"] += 1
        expected["table_minimum"] += len(
            re.findall(
                r"\\begin\s*\{(?:tabular\*?|tabularx|longtable|tblr|longtblr|tabu)\}",
                content_source,
            )
        )
        expected["list_minimum"] += len(
            re.findall(r"\\begin\s*\{(?:itemize|enumerate|description)\}", content_source)
        )
        if re.search(
            r"\\(?:part|chapter|section|subsection|subsubsection|frametitle)\*?(?![A-Za-z@])",
            content_source,
        ):
            expected["headings"] = True
    expected["formulas"] = bool(expected["formula_minimum"])
    expected["figures"] = bool(expected["figure_minimum"])
    expected["tables"] = bool(expected["table_minimum"])
    expected["lists"] = bool(expected["list_minimum"])
    return expected


def _escaped_for_project(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return bool(backslashes % 2)


def _write_support_file(path: Path, content: str) -> None:
    """Write a generated helper without clobbering a user-owned namesake."""

    if path.exists() or path.is_symlink():
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"Cannot inspect existing support file {path}: {error}") from error
        if existing != content:
            raise ValueError(
                f"Refusing to replace existing project file {path.name!r}; rename it and try again."
            )
        if path.is_symlink():
            path.unlink()
        else:
            return
    path.write_text(content, encoding="utf-8")


def _active_local_beamer_color_themes(
    text: str,
    root: Path,
    relative: Path,
    target_mode: str,
) -> list[str]:
    """Return local color themes selected by active presentation content."""

    active_view = _mask_definition_regions(
        mask_tex(_reduce_static_conditionals(text))
    )
    candidates: list[tuple[int, int, str, list[str]]] = []
    marker_index = 0
    for command_name in ("usetheme", "usecolortheme", "RequirePackage"):
        for command in iter_commands(active_view, command_name):
            value = group_content(active_view, command.mandatory) or ""
            packages: list[str] = []
            for raw_name in split_top_level(value):
                name = raw_name.strip()
                if command_name == "RequirePackage":
                    prefix = "beamercolortheme"
                    if not name.startswith(prefix):
                        continue
                    name = name[len(prefix) :]
                if re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    packages.append(f"beamercolortheme{name}")
            if not packages:
                continue
            marker_index += 1
            marker = f"CanvasAllyLocalColorThemeCandidate{marker_index:06d}"
            while marker in active_view:
                marker_index += 1
                marker = f"CanvasAllyLocalColorThemeCandidate{marker_index:06d}"
            candidates.append((command.start, command.end, marker, packages))

    marked_view = active_view
    for start, end, marker, _packages in sorted(candidates, reverse=True):
        marked_view = marked_view[:start] + marker + marked_view[end:]
    marked_view, _ = _preprocess_beamer_content(
        marked_view,
        relative,
        target_mode,
    )

    selected: list[str] = []
    for _start, _end, marker, packages in candidates:
        if marker not in marked_view:
            continue
        for package in packages:
            if package in selected:
                continue
            if any(root.rglob(package + ".sty")):
                selected.append(package)
    return selected


def prepare_project(paths: ProjectPaths, config: ToolConfig, *, force: bool = False) -> PrepareResult:
    """Copy a project and instrument every copied TeX source."""

    copy_project(paths, force=force)
    copied_main = paths.output / paths.main_relative
    findings: list[Finding] = []
    profile: str | None = None

    original_main = copied_main.read_text(encoding="utf-8")
    original_class, original_class_match = detect_document_class(original_main)
    migrating_beamer = original_class == "beamer" and config.beamer_mode == "ltx-talk"
    static_presentation = migrating_beamer or original_class == "ltx-talk"
    if static_presentation:
        class_options = group_content(original_main, original_class_match.optional) or ""
        normalized_options = {
            re.sub(r"\s+", "", item).lower()
            for item in split_top_level(class_options)
        }
        config.beamer_target_mode = (
            "handout"
            if normalized_options.intersection({"handout", "mode=handout"})
            else "projector"
        )
        config.local_beamer_color_themes = _active_local_beamer_color_themes(
            original_main,
            paths.output,
            paths.main_relative,
            config.beamer_target_mode,
        )

    tex_files, dependency_findings = _dependency_closure(
        copied_main,
        paths.output,
        strict=config.strict,
        beamer_target_mode=config.beamer_target_mode if static_presentation else None,
    )
    findings.extend(dependency_findings)

    placeholder_sources: list[str] = []
    for path in tex_files:
        source_view = path.read_text(encoding="utf-8")
        active_offsets: list[int] = []
        if path == copied_main:
            active_end = _active_document_end(
                source_view,
                config.beamer_target_mode if static_presentation else None,
            )
            if active_end is not None:
                active_offsets.append(active_end)
        endinput = _active_endinput(source_view)
        if endinput is not None:
            active_offsets.append(endinput)
        if active_offsets:
            source_view = source_view[: min(active_offsets)]
        if static_presentation:
            source_view, _ = _preprocess_beamer_content(
                source_view,
                path.relative_to(paths.output),
                config.beamer_target_mode,
            )
        source_view = _reduce_static_conditionals(source_view)
        placeholder_sources.append(source_view)
    config.stateful_graphic_default = any(
        _has_stateful_native_default(source, "graphic")
        for source in placeholder_sources
    )
    config.stateful_tikz_default = any(
        _has_stateful_native_default(source, "tikz")
        for source in placeholder_sources
    )
    placeholder_allocator = PlaceholderAllocator.from_sources(
        placeholder_sources,
        [value for value in config.image_alts.values() if value is not None],
    )
    config.reachable_visual_definitions = reachable_visual_definitions(
        placeholder_sources
    )
    config.tagged_theorem_environments = tagged_theorem_environment_names(
        placeholder_sources
    )

    for path in tex_files:
        _materialize_parent_symlinks(path, paths.output)
        if path.is_symlink():
            # Materialize source symlinks before writing so the original target
            # can never be modified through the copied link.
            linked_text = path.read_text(encoding="utf-8")
            path.unlink()
            path.write_text(linked_text, encoding="utf-8")
        path_text = path.read_text(encoding="utf-8")
        active_offsets: list[int] = []
        if path == copied_main:
            document_end = _active_document_end(
                path_text,
                config.beamer_target_mode if static_presentation else None,
            )
            if document_end is not None:
                active_offsets.append(document_end)
        endinput = _active_endinput(path_text)
        if endinput is not None:
            active_offsets.append(endinput)
        active_cut = min(active_offsets) if active_offsets else len(path_text)
        reduced_active = _reduce_static_conditionals(path_text[:active_cut])
        reduced_path_text = reduced_active + path_text[active_cut:]
        if reduced_path_text != path_text:
            path.write_text(reduced_path_text, encoding="utf-8")
        changed, file_findings, file_profile, _ = transform_tex_file(
            path,
            paths.output,
            config,
            is_main=path == copied_main,
            migrate_beamer=migrating_beamer,
            preprocess_presentation=static_presentation,
            active_end_offset=active_cut if active_offsets else None,
            placeholder_allocator=placeholder_allocator,
        )
        findings.extend(file_findings)
        if file_profile:
            profile = file_profile
        if changed is not None:
            path.write_text(changed, encoding="utf-8")

    if profile is None:
        raise RuntimeError("The selected main TeX file was not transformed")
    expectations = _source_expectations(
        tex_files,
        review_findings=findings if profile == "ltx-talk" else None,
        root=paths.output,
        strict=config.strict,
    )
    support_dir = copied_main.parent
    style_path = support_dir / "canvasally.sty"
    _write_support_file(style_path, CANVASALLY_STYLE)
    if profile == "ltx-talk":
        shim_path = support_dir / "canvasally-ltx-talk-shim.tex"
        _write_support_file(
            shim_path,
            LTX_TALK_SHIM,
        )
        content_shim_path = support_dir / "canvasally-ltx-talk-content-shim.tex"
        _write_support_file(content_shim_path, LTX_TALK_CONTENT_SHIM)

    deduplicated_findings: list[Finding] = []
    seen_unsafe_onlyenv: set[tuple[str | None, int | None]] = set()
    for finding in findings:
        if finding.code == "unsafe-onlyenv-tagging":
            key = (finding.file, finding.line)
            if key in seen_unsafe_onlyenv:
                continue
            seen_unsafe_onlyenv.add(key)
        deduplicated_findings.append(finding)
    findings = deduplicated_findings

    findings.insert(
        0,
        Finding(
            "project-copy",
            f"Copied {paths.source} to {paths.output}; the source was not modified.",
        ),
    )
    return PrepareResult(
        source=paths.source,
        output=paths.output,
        main_tex=copied_main,
        profile=profile,
        findings=findings,
        source_expectations=expectations,
    )
