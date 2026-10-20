from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from canvas_ally_latex.build import (
    BuildError,
    _materialize_file_symlinks,
    build_project,
)


class BuildEnvironmentTests(unittest.TestCase):
    def test_read_only_file_symlink_is_materialized_writable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "copy"
            external = Path(temporary) / "external.txt"
            linked = root / "linked.txt"
            root.mkdir()
            external.write_text("external content\n", encoding="utf-8")
            external.chmod(0o444)
            external_mode = stat.S_IMODE(external.stat().st_mode)
            linked.symlink_to(external)

            try:
                _materialize_file_symlinks(root)

                self.assertFalse(linked.is_symlink())
                self.assertEqual(linked.read_text(encoding="utf-8"), "external content\n")
                self.assertTrue(stat.S_IMODE(linked.stat().st_mode) & stat.S_IWUSR)
                self.assertEqual(
                    stat.S_IMODE(external.stat().st_mode),
                    external_mode,
                )
            finally:
                external.chmod(0o644)

    def test_failed_build_removes_copied_stale_and_partial_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / "main.tex"
            pdf = root / "main.pdf"
            main.write_text(
                "\\documentclass{article}\\begin{document}Test\\end{document}\n",
                encoding="utf-8",
            )
            pdf.write_bytes(b"%PDF-1.5 stale source output\n")

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                self.assertFalse(pdf.exists(), "the copied stale PDF must be removed first")
                pdf.write_bytes(b"%PDF-2.0 partial failed output\n")
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="intentional compiler failure",
                    stderr="",
                )

            def fake_which(name: str) -> str | None:
                return None if name == "kpsewhich" else "/usr/bin/tool"

            with (
                patch("canvas_ally_latex.build.shutil.which", side_effect=fake_which),
                patch("canvas_ally_latex.build.subprocess.run", side_effect=fake_run),
            ):
                with self.assertRaises(BuildError):
                    build_project(root, main)

            self.assertFalse(pdf.exists())

    def test_timed_out_build_removes_partial_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / "main.tex"
            pdf = root / "main.pdf"
            main.write_text(
                "\\documentclass{article}\\begin{document}Test\\end{document}\n",
                encoding="utf-8",
            )

            def fake_run(command: list[str], **_kwargs: object) -> None:
                pdf.write_bytes(b"%PDF-2.0 partial timed-out output\n")
                raise subprocess.TimeoutExpired(command, 900)

            def fake_which(name: str) -> str | None:
                return None if name == "kpsewhich" else "/usr/bin/tool"

            with (
                patch("canvas_ally_latex.build.shutil.which", side_effect=fake_which),
                patch("canvas_ally_latex.build.subprocess.run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(BuildError, "exceeded the 15-minute"):
                    build_project(root, main)

            self.assertFalse(pdf.exists())

    def test_subprocess_start_error_removes_pdf_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / "main.tex"
            pdf = root / "main.pdf"
            main.write_text(
                "\\documentclass{article}\\begin{document}Test\\end{document}\n",
                encoding="utf-8",
            )

            def fake_run(command: list[str], **_kwargs: object) -> None:
                pdf.write_bytes(b"%PDF-2.0 partial launch-error output\n")
                raise OSError("intentional launch failure")

            def fake_which(name: str) -> str | None:
                return None if name == "kpsewhich" else "/usr/bin/tool"

            with (
                patch("canvas_ally_latex.build.shutil.which", side_effect=fake_which),
                patch("canvas_ally_latex.build.subprocess.run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(BuildError, "Could not start LuaLaTeX"):
                    build_project(root, main)

            self.assertFalse(pdf.exists())

    def test_luatex_cache_paths_exist_before_latexmk_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / "main.tex"
            main.write_text(
                "\\documentclass{article}\\begin{document}Test\\end{document}\n",
                encoding="utf-8",
            )

            def fake_run(
                command: list[str],
                *,
                cwd: Path,
                env: dict[str, str],
                check: bool,
                capture_output: bool,
                text: bool,
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                self.assertTrue(Path(env["TEXMFVAR"]).is_dir())
                cache_paths = env["TEXMFCACHE"].split(os.pathsep)
                self.assertTrue(Path(cache_paths[0]).is_dir())
                # Luaotfload on TeX Live 2026 rejects the otherwise-valid
                # configuration when these writable roots differ.
                self.assertEqual(Path(env["TEXMFVAR"]), Path(cache_paths[0]))
                if "-lualatex" in command:
                    (root / "main.pdf").write_bytes(b"%PDF-2.0\n")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            def fake_which(name: str) -> str | None:
                return None if name == "kpsewhich" else "/usr/bin/tool"

            with (
                patch("canvas_ally_latex.build.shutil.which", side_effect=fake_which),
                patch("canvas_ally_latex.build.subprocess.run", side_effect=fake_run),
            ):
                pdf, log = build_project(root, main)

            self.assertEqual(pdf.resolve(), (root / "main.pdf").resolve())
            self.assertTrue(log.is_file())
            self.assertFalse((root / "canvas-ally-cache").exists())

    def test_generated_outputs_do_not_write_through_copied_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "copy"
            external = Path(temporary) / "external"
            root.mkdir()
            external.mkdir()
            main = root / "main.tex"
            main.write_text(
                "\\documentclass{article}\\begin{document}Test\\end{document}\n",
                encoding="utf-8",
            )
            external_pdf = external / "original.pdf"
            external_log = external / "original.log"
            external_pdf.write_bytes(b"ORIGINAL PDF")
            external_log.write_text("ORIGINAL LOG", encoding="utf-8")
            (root / "main.pdf").symlink_to(external_pdf)
            (root / "canvas-ally-build.log").symlink_to(external_log)

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                if "-lualatex" in command:
                    (root / "main.pdf").write_bytes(b"%PDF-2.0\n")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            def fake_which(name: str) -> str | None:
                return None if name == "kpsewhich" else "/usr/bin/tool"

            with (
                patch("canvas_ally_latex.build.shutil.which", side_effect=fake_which),
                patch("canvas_ally_latex.build.subprocess.run", side_effect=fake_run),
            ):
                build_project(root, main)

            self.assertEqual(external_pdf.read_bytes(), b"ORIGINAL PDF")
            self.assertEqual(external_log.read_text(encoding="utf-8"), "ORIGINAL LOG")
            self.assertFalse((root / "main.pdf").is_symlink())
            self.assertFalse((root / "canvas-ally-build.log").is_symlink())


if __name__ == "__main__":
    unittest.main()
