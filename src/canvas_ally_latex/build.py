"""LuaLaTeX build support for an instrumented project copy."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


class BuildError(RuntimeError):
    """Raised when the LaTeX toolchain cannot produce the requested PDF."""


def _safe_fallback_cache() -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    """Return an owner-only persistent cache, or a secure per-run fallback."""

    user_id = getattr(os, "getuid", lambda: 0)()
    cache = Path(tempfile.gettempdir()) / f"canvas-ally-latex-cache-v1-{user_id}"
    try:
        cache.mkdir(mode=0o700)
    except FileExistsError:
        metadata = cache.lstat()
        safe = (
            stat.S_ISDIR(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == user_id
        )
        if safe:
            cache.chmod(0o700)
            return cache, None
        temporary = tempfile.TemporaryDirectory(prefix="canvas-ally-latex-cache-")
        one_run = Path(temporary.name)
        one_run.chmod(0o700)
        return one_run, temporary
    return cache, None


def _unlink_generated_symlinks(build_directory: Path, stem: str, project_root: Path) -> None:
    """Ensure TeX and tool-owned outputs cannot write through copied symlinks."""

    suffixes = (
        ".aux",
        ".bbl",
        ".bcf",
        ".blg",
        ".fdb_latexmk",
        ".fls",
        ".log",
        ".nav",
        ".out",
        ".pdf",
        ".run.xml",
        ".snm",
        ".synctex.gz",
        ".toc",
        ".xdv",
    )
    paths = [build_directory / f"{stem}{suffix}" for suffix in suffixes]
    paths.append(project_root / "canvas-ally-build.log")
    for path in paths:
        if path.is_symlink():
            path.unlink()


def _remove_pdf_candidate(path: Path) -> None:
    """Remove the exact generated-PDF candidate without following symlinks."""

    if path.is_symlink():
        path.unlink()
        return
    if not path.exists():
        return
    if not path.is_file():
        raise BuildError(f"Expected generated PDF path is not a regular file: {path}")
    path.unlink()


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".canvas-ally-build-",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(content)
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _materialize_file_symlinks(project_root: Path) -> None:
    """Copy linked files into the build copy and reject linked directories."""

    for directory, directory_names, file_names in os.walk(project_root, followlinks=False):
        parent = Path(directory)
        for name in directory_names:
            path = parent / name
            if path.is_symlink():
                raise BuildError(
                    f"Copied project contains a directory symlink that is unsafe to build: {path}. "
                    "Materialize that directory in the source project first."
                )
        for name in file_names:
            path = parent / name
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise BuildError(f"Copied project contains a broken/cyclic symlink: {path}") from error
            if not target.is_file():
                raise BuildError(f"Copied project symlink does not target a regular file: {path}")
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".canvas-ally-linked-file-",
                    dir=path.parent,
                    delete=False,
                ) as stream:
                    temporary_name = stream.name
                shutil.copy2(target, temporary_name, follow_symlinks=True)
                temporary = Path(temporary_name)
                copied_mode = stat.S_IMODE(temporary.lstat().st_mode)
                temporary.chmod(
                    copied_mode | stat.S_IRUSR | stat.S_IWUSR,
                    follow_symlinks=False,
                )
                os.replace(temporary_name, path)
                temporary_name = None
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)


def build_project(
    project_root: Path,
    main_tex: Path,
    *,
    keep_build_files: bool = False,
) -> tuple[Path, Path]:
    """Build twice as needed through latexmk and return PDF and captured log paths."""

    latexmk = shutil.which("latexmk")
    lualatex = shutil.which("lualatex")
    if not latexmk or not lualatex:
        missing = [name for name, value in (("latexmk", latexmk), ("lualatex", lualatex)) if not value]
        raise BuildError("Missing LaTeX command(s): " + ", ".join(missing))

    project_root = project_root.resolve()
    main_tex = main_tex.resolve()
    try:
        relative_main = main_tex.relative_to(project_root)
    except ValueError as error:
        raise BuildError("Main TeX file must be inside the copied project") from error

    environment = os.environ.copy()
    temporary_cache: tempfile.TemporaryDirectory[str] | None = None
    kpsewhich = shutil.which("kpsewhich")
    inherited_cache = ""
    if kpsewhich:
        try:
            inherited_cache = subprocess.check_output(
                [kpsewhich, "-var-value=TEXMFCACHE"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            inherited_cache = ""
    default_writable = False
    for raw_path in inherited_cache.split(os.pathsep):
        if not raw_path.strip():
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.is_dir():
            continue
        try:
            with tempfile.NamedTemporaryFile(prefix="canvas-ally-probe-", dir=candidate):
                default_writable = True
                break
        except OSError:
            continue
    if not default_writable:
        cache, temporary_cache = _safe_fallback_cache()
        environment["TEXMFVAR"] = str(cache)
        environment["TEXMFCACHE"] = str(cache) + (
            os.pathsep + inherited_cache if inherited_cache else ""
        )

    inherited_inputs = environment.get("TEXINPUTS", "")
    environment["TEXINPUTS"] = str(project_root) + "//" + os.pathsep + inherited_inputs

    build_directory = main_tex.parent
    _materialize_file_symlinks(project_root)
    _unlink_generated_symlinks(build_directory, main_tex.stem, project_root)
    candidate_pdf = main_tex.with_suffix(".pdf")
    # A source project may ship an older PDF beside its TeX. The copied file
    # must not survive a failed rebuild and masquerade as this run's accessible
    # output.
    _remove_pdf_candidate(candidate_pdf)

    command = [
        latexmk,
        "-norc",
        "-lualatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        "-synctex=0",
        main_tex.name,
    ]
    try:
        process = subprocess.run(
            command,
            cwd=build_directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    except subprocess.TimeoutExpired as error:
        _remove_pdf_candidate(candidate_pdf)
        if temporary_cache is not None:
            temporary_cache.cleanup()
        raise BuildError("LuaLaTeX build exceeded the 15-minute safety timeout") from error
    except OSError as error:
        _remove_pdf_candidate(candidate_pdf)
        if temporary_cache is not None:
            temporary_cache.cleanup()
        raise BuildError(f"Could not start LuaLaTeX build: {error}") from error
    build_log = project_root / "canvas-ally-build.log"
    if process.returncode != 0:
        _remove_pdf_candidate(candidate_pdf)
    try:
        _atomic_write_text(
            build_log,
            "$ " + " ".join(command) + "\n\nSTDOUT\n" + process.stdout + "\nSTDERR\n" + process.stderr,
        )
    except OSError as error:
        _remove_pdf_candidate(candidate_pdf)
        if temporary_cache is not None:
            temporary_cache.cleanup()
        raise BuildError(f"Could not write LuaLaTeX build log: {error}") from error
    if process.returncode != 0:
        tail = "\n".join((process.stdout + "\n" + process.stderr).splitlines()[-35:])
        if temporary_cache is not None:
            temporary_cache.cleanup()
        raise BuildError(
            f"LuaLaTeX build failed (see {build_log}):\n{tail}"
        )

    pdf = candidate_pdf
    if not pdf.is_file():
        if temporary_cache is not None:
            temporary_cache.cleanup()
        raise BuildError(f"Build completed but did not create {pdf}")

    if not keep_build_files:
        try:
            cleanup = subprocess.run(
                [latexmk, "-norc", "-c", main_tex.name],
                cwd=build_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            cleanup = None
        if cleanup is None or cleanup.returncode != 0:
            with build_log.open("a", encoding="utf-8") as stream:
                detail = "cleanup timed out" if cleanup is None else cleanup.stdout + cleanup.stderr
                stream.write("\nCLEANUP WARNING\n" + detail)
    if temporary_cache is not None:
        temporary_cache.cleanup()
    return pdf, build_log
