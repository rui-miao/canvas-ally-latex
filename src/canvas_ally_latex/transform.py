"""Small, conservative source transformations for accessible tagged LaTeX."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import ToolConfig
from .model import Finding


BEGIN_MARKER = "% BEGIN canvas-ally-latex generated content"
END_MARKER = "% END canvas-ally-latex generated content"

PROTECTED_ENVIRONMENTS = (
    "Verbatim",
    "comment",
    "filecontents",
    "filecontents*",
    "lstlisting",
    "minted",
    "semiverbatim",
    "tcblisting",
    "verbatim",
    "verbatim*",
)

TABLE_ENVIRONMENTS = ("tabular", "tabular*", "tabularx", "longtable")

ACTION_ALIGNMENT_ENVIRONMENTS = (
    *TABLE_ENVIRONMENTS,
    "align",
    "align*",
    "alignat",
    "alignat*",
    "aligned",
    "alignedat",
    "array",
    "Bmatrix",
    "bmatrix",
    "cases",
    "dcases",
    "dcases*",
    "eqnarray",
    "eqnarray*",
    "flalign",
    "flalign*",
    "matrix",
    "pmatrix",
    "smallmatrix",
    "split",
    "Vmatrix",
    "vmatrix",
)

UNSUPPORTED_VISUAL_COMMANDS = {
    "includepdf": "Imported PDF pages do not retain their original tag tree; provide an accessible linked file or rebuild the content in LaTeX.",
    "includepdfmerge": "Merged PDF pages do not retain a verified tag tree; provide accessible linked files or rebuild the content in LaTeX.",
    "includestandalone": "Standalone TeX graphics do not have a verified native tagging path; export them and use \\includegraphics with meaningful alt text.",
    "includeinkscape": "Inkscape graphics do not have a verified native tagging path; export them and use \\includegraphics with meaningful alt text.",
    "includesvg": "SVG inclusion is not instrumented; export it as a graphic and use \\includegraphics with meaningful alt text.",
    "pgfpicture": "Command-form PGF pictures need a manually authored textual alternative and verified PDF tagging.",
    "pgfimage": "\\pgfimage is not instrumented; use \\includegraphics with meaningful alt text.",
    "pgfuseimage": "\\pgfuseimage is not instrumented; use \\includegraphics with meaningful alt text.",
    "multiinclude": "Incremental multi-page graphics require a meaningful static alternative for the accessible PDF.",
    "epsfig": "Legacy \\epsfig is not instrumented; use \\includegraphics with meaningful alt text.",
    "epsfbox": "Legacy \\epsfbox is not instrumented; use \\includegraphics with meaningful alt text.",
    "psfig": "Legacy \\psfig is not instrumented; use \\includegraphics with meaningful alt text.",
    "pspicture": "Command-form PSTricks pictures need a manually authored textual alternative.",
    "animategraphics": "Animated graphics require a meaningful static alternative for the accessible PDF.",
    "includemedia": "Embedded media requires an accessible static alternative and equivalent linked content.",
    "includemovie": "Embedded movies require an accessible static alternative and equivalent linked content.",
    "movie": "Embedded movies require an accessible static alternative and equivalent linked content.",
    "adjustimage": "\\adjustimage is not instrumented; use \\includegraphics with meaningful alt text.",
    "adjincludegraphics": "\\adjincludegraphics is not instrumented; use \\includegraphics with meaningful alt text.",
    "asyinclude": "Included Asymptote graphics need a manually authored textual alternative.",
}

UNSUPPORTED_VISUAL_ENVIRONMENTS = {
    "pspicture": "PSTricks pictures need a manually authored textual alternative.",
    "pspicture*": "PSTricks pictures need a manually authored textual alternative.",
    "asy": "Asymptote pictures need a manually authored textual alternative.",
    "pgfpicture": "Low-level PGF pictures need a manually authored textual alternative and verified PDF tagging.",
    "overpic": "Overlaid picture content needs a manually authored textual alternative and verified reading order.",
    "Overpic": "Overlaid picture content needs a manually authored textual alternative and verified reading order.",
    "animateinline": "Inline animations require a meaningful static alternative for the accessible PDF.",
    "forest": "Forest diagrams need a manually authored textual alternative and verified PDF tagging.",
    "tikzcd": "TikZ commutative diagrams need a manually authored textual alternative and verified PDF tagging.",
    "circuitikz": "Circuit diagrams need a manually authored textual alternative and verified PDF tagging.",
    "ganttchart": "Gantt charts need a manually authored textual alternative and verified PDF tagging.",
}

DYNAMIC_VISUAL_COMMANDS = tuple(
    sorted(
        {"includegraphics", "tikz", *UNSUPPORTED_VISUAL_COMMANDS},
        key=len,
        reverse=True,
    )
)

CANVASALLY_STYLE = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{canvasally}[2026/09/11 Canvas Ally tagged-PDF support]
\RequirePackage{iftex}
\ifLuaTeX\else
  \PackageError{canvasally}{LuaLaTeX is required for tagged PDF output}
    {Compile with latexmk -lualatex or lualatex.}
\fi
\tagpdfsetup{
  tabsorder=structure,
  math/mathml/luamml/load=true,
  math/alt/use=true
}
\makeatletter
\@ifpackageloaded{hyperref}{}{\RequirePackage{hyperref}}
\makeatother
\providecommand\CanvasAllyTitle{}
\providecommand\CanvasAllyLanguage{en-US}
\newcommand\CanvasAllyApplyMetadata{%
  \hypersetup{
    pdftitle={\CanvasAllyTitle},
    pdflang={\CanvasAllyLanguage},
    pdfdisplaydoctitle=true
  }%
}
% Tagged amsthm currently misparses \cite[note]{key} when it appears directly
% in a theorem's optional heading.  This identity wrapper delays that citation
% until the heading is typeset without changing its rendered content.
\ProvideDocumentCommand\CanvasAllyTheoremNote{m}{#1}
% Optional author override: place immediately before the named math environment.
% Example: \CanvasAllyNextMathAlt{equation*}{The risk ratio equals ...}
\ExplSyntaxOn
\NewDocumentCommand\CanvasAllyNextMathAlt{m m}
  {\AddToHookNext{env/#1/begin}{\tl_set:Nn \l__math_content_template_tl {#2}}}
\ExplSyntaxOff
"""

LTX_TALK_SHIM = r"""% Compatibility helpers generated by canvas-ally-latex.
% ltx-talk intentionally implements a subset of Beamer.  These commands remove
% visual-only Beamer configuration; content commands remain visible to LaTeX.
\tagpdfsetup{role/new-tag={frametitle/H1}}
\ProvideDocumentCommand{\usetheme}{O{}m}{}
\ProvideDocumentCommand{\usecolortheme}{O{}m}{}
\ProvideDocumentCommand{\usefonttheme}{O{}m}{}
\ProvideDocumentCommand{\useinnertheme}{O{}m}{}
\ProvideDocumentCommand{\useoutertheme}{O{}m}{}
\ProvideDocumentCommand{\setbeamertemplate}{m o +g}{}
\ProvideDocumentCommand{\defbeamertemplate}{D<>{all} s m m o o +m o +g}{}
\ProvideDocumentCommand{\defbeamertemplateparent}{m o m o o +m}{}
\ProvideDocumentCommand{\addtobeamertemplate}{m m m}{}
\ProvideDocumentCommand{\setbeamercolor}{s m m}{}
\ProvideDocumentCommand{\setbeamerfont}{s m m}{}
\ProvideDocumentCommand{\setbeamercovered}{m}{}
\ProvideDocumentCommand{\setbeamersize}{m}{}
\ProvideDocumentCommand{\setbeameroption}{m}{}
\ProvideDocumentCommand{\beamerdefaultoverlayspecification}{m}{}
\ProvideDocumentCommand{\beamertemplatenavigationsymbolsempty}{}{}
\ProvideDocumentCommand{\logo}{m}{}
\ProvideDocumentCommand{\institute}{O{}m}{}
\ProvideDocumentCommand{\subtitle}{O{}m}{}
\ProvideDocumentCommand{\titlegraphic}{m}{}
\ProvideDocumentCommand{\subject}{m}{}
\ProvideDocumentCommand{\keywords}{m}{}
\ProvideDocumentCommand{\AtBeginSection}{o m}{}
\ProvideDocumentCommand{\AtBeginSubsection}{o m}{}
\ProvideDocumentCommand{\AtBeginPart}{o m}{}
\ProvideDocumentCommand{\AtBeginLecture}{o m}{}
\ProvideDocumentCommand{\AtBeginNote}{m}{}
\ProvideDocumentCommand{\AtEndNote}{m}{}
% Beamer keeps the standard legacy font switches available. Older decks use
% them in both text and math, including inside macros replayed for MathML.
\makeatletter
\DeclareOldFontCommand{\rm}{\normalfont\rmfamily}{\mathrm}
\DeclareOldFontCommand{\sf}{\normalfont\sffamily}{\mathsf}
\DeclareOldFontCommand{\tt}{\normalfont\ttfamily}{\mathtt}
\DeclareOldFontCommand{\bf}{\normalfont\bfseries}{\mathbf}
\DeclareOldFontCommand{\it}{\normalfont\itshape}{\mathit}
\DeclareOldFontCommand{\sl}{\normalfont\slshape}{\@nomath\sl}
\DeclareOldFontCommand{\sc}{\normalfont\scshape}{\@nomath\sc}
\makeatother
\ProvideDocumentCommand{\lecture}{O{} m m}{}
\ProvideDocumentCommand{\includeonlylecture}{m}{}
\ProvideDocumentCommand{\note}{D<>{all} O{} +m}{}
\ProvideDocumentCommand{\structure}{D<>{all} +m}{{\color<#1>{structure}#2}}
\ProvideDocumentCommand{\beamerbutton}{m}{#1}
\ProvideDocumentCommand{\beamergotobutton}{m}{#1}
\ProvideDocumentCommand{\beamerskipbutton}{m}{#1}
\ProvideDocumentCommand{\beamerreturnbutton}{m}{#1}

% Preserve Beamer's public overlay-aware declaration behavior. Ordinary
% declarations still go through the LaTeX kernel; only \newcommand<> and
% \renewcommand<> use this collector. The overlay may occur at any argument
% boundary, and the final one supplied is appended as the worker's last arg.
\makeatletter
\let\CanvasAllyKernelNewCommand\newcommand
\let\CanvasAllyKernelRenewCommand\renewcommand
\def\newcommand{%
  \@ifnextchar<\CanvasAllyDeclareNew\CanvasAllyKernelNewCommand}
\def\renewcommand{%
  \@ifnextchar<\CanvasAllyDeclareRenew\CanvasAllyKernelRenewCommand}
\def\CanvasAllyDeclareNew<>{%
  \@ifstar
    {\CanvasAllyReadDeclaration\CanvasAllyInstallNew{1}}%
    {\CanvasAllyReadDeclaration\CanvasAllyInstallNew{0}}}
\def\CanvasAllyDeclareRenew<>{%
  \@ifstar
    {\CanvasAllyReadDeclaration\CanvasAllyInstallRenew{1}}%
    {\CanvasAllyReadDeclaration\CanvasAllyInstallRenew{0}}}
\long\def\CanvasAllyReadDeclaration#1#2#3{%
  \@ifnextchar[
    {\CanvasAllyReadArgumentCount{#1}{#2}{#3}}%
    {\CanvasAllyReadPlainBody{#1}{#2}{#3}{0}}}
\def\CanvasAllyReadArgumentCount#1#2#3[#4]{%
  \@ifnextchar[
    {\CanvasAllyReadDefault{#1}{#2}{#3}{#4}}%
    {\CanvasAllyReadPlainBody{#1}{#2}{#3}{#4}}}
\long\def\CanvasAllyReadPlainBody#1#2#3#4#5{%
  \CanvasAllyFinishDeclaration{#1}{#2}{#3}{#4}{0}{}{#5}}
\long\def\CanvasAllyReadDefault#1#2#3#4[#5]#6{%
  \CanvasAllyFinishDeclaration{#1}{#2}{#3}{#4}{1}{#5}{#6}}
\long\def\CanvasAllyFinishDeclaration#1#2#3#4#5#6#7{%
  \ifnum#4>8\relax
    \PackageError{canvasally}{Overlay-aware commands support at most 8 normal arguments}%
      {The ninth argument slot is reserved for the overlay specification.}%
  \else
    \ifnum#5=1\relax
      \ifnum#4<1\relax
        \PackageError{canvasally}{An optional default requires at least one argument}{}%
      \else
        #1{#3}{#4}{#5}{#6}{#7}{#2}%
      \fi
    \else
      #1{#3}{#4}{#5}{#6}{#7}{#2}%
    \fi
  \fi}
\def\CanvasAllyKernelNewCommandStar#1{\CanvasAllyKernelNewCommand*#1}
\def\CanvasAllyKernelRenewCommandStar#1{\CanvasAllyKernelRenewCommand*#1}
\long\def\CanvasAllyInstallNew#1#2#3#4#5#6{%
  \@ifdefinable#1{%
    \CanvasAllyDefineNewWorker{#1}{#2}{#5}{#6}%
    \CanvasAllyDefinePublic{#1}{#2}{#3}{#4}}}
\long\def\CanvasAllyInstallRenew#1#2#3#4#5#6{%
  \ifdefined#1
    \ifcsname CanvasAllyOriginal@\string#1\endcsname\else
      \expandafter\let\csname CanvasAllyOriginal@\string#1\endcsname#1%
    \fi
    \ifcsname CanvasAllyOverlayWorker@\string#1\endcsname
      \CanvasAllyRenewWorker{#1}{#2}{#5}{#6}%
    \else
      \CanvasAllyDefineNewWorker{#1}{#2}{#5}{#6}%
    \fi
    \CanvasAllyDefinePublic{#1}{#2}{#3}{#4}%
  \else
    \PackageError{canvasally}{Command \string#1 undefined}%
      {Use \string\newcommand<> for a new command.}%
  \fi}
\long\def\CanvasAllyDefineNewWorker#1#2#3#4{%
  \ifnum#4=1\relax
    \expandafter\CanvasAllyKernelNewCommandStar
      \csname CanvasAllyOverlayWorker@\string#1\endcsname
      [\number\numexpr#2+1\relax]{#3}%
  \else
    \expandafter\CanvasAllyKernelNewCommand
      \csname CanvasAllyOverlayWorker@\string#1\endcsname
      [\number\numexpr#2+1\relax]{#3}%
  \fi}
\long\def\CanvasAllyRenewWorker#1#2#3#4{%
  \ifnum#4=1\relax
    \expandafter\CanvasAllyKernelRenewCommandStar
      \csname CanvasAllyOverlayWorker@\string#1\endcsname
      [\number\numexpr#2+1\relax]{#3}%
  \else
    \expandafter\CanvasAllyKernelRenewCommand
      \csname CanvasAllyOverlayWorker@\string#1\endcsname
      [\number\numexpr#2+1\relax]{#3}%
  \fi}
\long\def\CanvasAllyDefinePublic#1#2#3#4{%
  \protected\def#1{%
    \CanvasAllyOverlayStart{\string#1}{#2}{#3}{#4}}}
\long\def\CanvasAllyOverlayStart#1#2#3#4{%
  \expandafter\CanvasAllyOverlayStartWorker
    \csname CanvasAllyOverlayWorker@#1\endcsname{#2}{#3}{#4}}
\long\def\CanvasAllyOverlayStartWorker#1#2#3#4{%
  \ifnum#3=1\relax
    \let\CanvasAllyStartAction\CanvasAllyScanOptional
  \else
    \let\CanvasAllyStartAction\CanvasAllyScanRequired
  \fi
  \CanvasAllyStartAction{#1}{#2}{#4}}
\long\def\CanvasAllyScanRequired#1#2#3{%
  \CanvasAllyCollect{#2}{#1}{}{}}
\long\def\CanvasAllyScanOptional#1#2#3{%
  \CanvasAllyOptionalCheck{#1}{#2}{}{#3}}
\long\def\CanvasAllyOptionalCheck#1#2#3#4{%
  \@ifnextchar<
    {\CanvasAllyReadOptionalOverlay{#1}{#2}{#4}}%
    {\@ifnextchar[
       {\CanvasAllyReadOptionalValue{#1}{#2}{#3}{#4}}%
       {\CanvasAllyUseOptionalDefault{#1}{#2}{#3}{#4}}}}
\long\def\CanvasAllyReadOptionalOverlay#1#2#3<#4>{%
  \CanvasAllyOptionalCheck{#1}{#2}{<#4>}{#3}}
\long\def\CanvasAllyReadOptionalValue#1#2#3#4[#5]{%
  \CanvasAllyCollect{\number\numexpr#2-1\relax}{#1}{#3}{{#5}}}
\long\def\CanvasAllyUseOptionalDefault#1#2#3#4{%
  \CanvasAllyCollect{\number\numexpr#2-1\relax}{#1}{#3}{{#4}}}
\long\def\CanvasAllyCollect#1#2#3#4{%
  \ifnum#1=0\relax
    \let\CanvasAllyCollectAction\CanvasAllyFinishCheck
  \else
    \let\CanvasAllyCollectAction\CanvasAllyArgumentCheck
  \fi
  \CanvasAllyCollectAction{#1}{#2}{#3}{#4}}
\long\def\CanvasAllyArgumentCheck#1#2#3#4{%
  \@ifnextchar<
    {\CanvasAllyReadOverlay{#1}{#2}{#4}}%
    {\CanvasAllyReadArgument{#1}{#2}{#3}{#4}}}
\long\def\CanvasAllyReadOverlay#1#2#3<#4>{%
  \CanvasAllyCollect{#1}{#2}{<#4>}{#3}}
\long\def\CanvasAllyReadArgument#1#2#3#4#5{%
  \CanvasAllyCollect{\number\numexpr#1-1\relax}{#2}{#3}{#4{#5}}}
% The final lookahead intentionally does not skip a trailing space.
\long\def\CanvasAllyIfNextRaw#1#2{%
  \def\CanvasAllyRawYes{#1}%
  \def\CanvasAllyRawNo{#2}%
  \futurelet\CanvasAllyRawToken\CanvasAllyIfNextRawAux}
\def\CanvasAllyIfNextRawAux{%
  \ifx\CanvasAllyRawToken<%
    \let\CanvasAllyRawAction\CanvasAllyRawYes
  \else
    \let\CanvasAllyRawAction\CanvasAllyRawNo
  \fi
  \CanvasAllyRawAction}
\long\def\CanvasAllyFinishCheck#1#2#3#4{%
  \CanvasAllyIfNextRaw
    {\CanvasAllyReadFinalOverlay{#2}{#4}}%
    {\CanvasAllyInvokeWorker{#2}{#4}{#3}}}
\long\def\CanvasAllyReadFinalOverlay#1#2<#3>{%
  \CanvasAllyInvokeWorker{#1}{#2}{<#3>}}
\long\def\CanvasAllyInvokeWorker#1#2#3{#1#2{#3}}
\providecommand\beameroriginal[1]{%
  \ifcsname CanvasAllyOriginal@\string#1\endcsname
    \csname CanvasAllyOriginal@\string#1\endcsname
  \else
    #1%
  \fi}
\makeatother
"""

LTX_TALK_CONTENT_SHIM = r"""% Late content helpers generated by canvas-ally-latex.
\NewCommandCopy\CanvasAllyHyperlink\hyperlink
\NewCommandCopy\CanvasAllyHypertarget\hypertarget
\RenewDocumentCommand{\hyperlink}{D<>{all} m m}
  {\only<#1>{\CanvasAllyHyperlink{#2}{#3}}}
\RenewDocumentCommand{\hypertarget}{D<>{all} m m}
  {\only<#1>{\CanvasAllyHypertarget{#2}{#3}}}
% A checkmark used in text must select the math font. Without this wrapper,
% a monospaced TikZ node can emit a .notdef glyph and invalidate PDF/UA.
\makeatletter
\@ifundefined{checkmark}{}{%
  \let\CanvasAllyMathCheckmark\checkmark
  \RenewDocumentCommand{\checkmark}{}{\ensuremath{\CanvasAllyMathCheckmark}}%
}
\makeatother
\makeatletter
\@ifundefined{semiverbatim}{%
  \newenvironment{semiverbatim}{%
    \trivlist\item\relax
    \leftskip\@totalleftmargin\rightskip\z@skip
    \parindent\z@\parfillskip\@flushglue\parskip\z@skip
    \@@par\@tempswafalse
    \def\par{\if@tempswa\leavevmode\null\@@par\penalty\interlinepenalty
      \else\@tempswatrue\ifhmode\@@par\penalty\interlinepenalty\fi\fi}%
    \obeylines\verbatim@font
    \def\dospecials{\do\ \do\$\do\&\do\#\do\^\do\_\do\%\do\~\do\`\do\,\do\'\do\-}%
    \let\do\@makeother\dospecials
    \def\\{\char`\\}\def\{{\char`\{}\def\}{\char`\}}%
    \frenchspacing\@vobeyspaces
    \everypar\expandafter{\the\everypar\unpenalty}%
  }{\endtrivlist}%
}{}
\makeatother
\ProvideDocumentEnvironment{alertblock}{D<>{all} m}
  {\begin{block}<#1>{#2}}{\end{block}}
\ProvideDocumentEnvironment{exampleblock}{D<>{all} m}
  {\begin{block}<#1>{#2}}{\end{block}}
\ProvideDocumentEnvironment{theorem}{D<>{all} o}
  {\begin{block}<#1>{Theorem\IfValueT{#2}{: #2}}}{\end{block}}
\ProvideDocumentEnvironment{definition}{D<>{all} o}
  {\begin{block}<#1>{Definition\IfValueT{#2}{: #2}}}{\end{block}}
\ProvideDocumentEnvironment{example}{D<>{all} o}
  {\begin{block}<#1>{Example\IfValueT{#2}{: #2}}}{\end{block}}
\ProvideDocumentEnvironment{proof}{O{Proof}}
  {\begin{block}{#1}}{\end{block}}
"""


class UnsupportedDocumentError(ValueError):
    """Raised when a class or migration mode is deliberately unsupported."""


@dataclass(slots=True)
class CommandMatch:
    """Offsets for a TeX command and its balanced arguments."""

    start: int
    end: int
    name: str
    optional: tuple[int, int] | None
    mandatory: tuple[int, int] | None
    starred: bool = False


@dataclass(slots=True)
class GraphicCommandMatch:
    """Offsets for graphicx/Beamer's star, overlay, options, and path."""

    start: int
    end: int
    starred: bool
    overlay: tuple[int, int] | None
    optionals: tuple[tuple[int, int], ...]
    mandatory: tuple[int, int]
    option_insert: int


@dataclass(slots=True)
class PlaceholderAllocator:
    """Allocate stable, project-wide numbered alternative-text placeholders."""

    maxima: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_sources(
        cls,
        sources: list[str],
        configured_alts: list[str] | None = None,
    ) -> "PlaceholderAllocator":
        allocator = cls()
        pattern = re.compile(
            r"(?<![A-Za-z0-9_-])(tikz|graphic|picture|visual)([1-9][0-9]*)(?![A-Za-z0-9_-])",
            re.I,
        )
        for source in sources + (configured_alts or []):
            for match in pattern.finditer(source):
                kind = match.group(1).lower()
                allocator.maxima[kind] = max(
                    allocator.maxima.get(kind, 0),
                    int(match.group(2)),
                )
        return allocator

    def allocate(self, kind: str) -> str:
        kind = kind.lower()
        number = self.maxima.get(kind, 0) + 1
        self.maxima[kind] = number
        return f"{kind}{number}"


@dataclass(slots=True)
class AltDirective:
    """One immediately preceding ``allytex`` source directive."""

    alt: str | None
    start: int
    end: int
    indent: str


@dataclass(slots=True)
class NativeDrawingMatch:
    """Offsets for a TikZ command/environment or native picture environment."""

    start: int
    end: int
    option_insert: int
    option_group: tuple[int, int] | None
    object_name: str
    kind: str


@dataclass(slots=True)
class UnsupportedVisualEnvironmentMatch:
    """Offsets for a visual environment without a verified native tag API."""

    start: int
    end: int
    name: str


@dataclass(slots=True)
class VisualDefinitionMatch:
    """A macro/environment definition and the ranges that form its body."""

    start: int
    end: int
    content_ranges: tuple[tuple[int, int], ...]


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _escaped(text: str, index: int) -> bool:
    slashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        slashes += 1
        index -= 1
    return bool(slashes % 2)


def mask_tex(text: str) -> str:
    """Blank comments and literal content while preserving offsets/newlines."""

    chars = list(text)
    size = len(text)

    def blank(start: int, end: int) -> None:
        for pos in range(start, min(end, size)):
            if chars[pos] not in "\r\n":
                chars[pos] = " "

    env_names = "|".join(
        re.escape(name)
        for name in sorted(PROTECTED_ENVIRONMENTS, key=len, reverse=True)
    )
    env_re = re.compile(r"\\begin\s*\{(" + env_names + r")\}")
    literal_re = re.compile(
        r"\\(?:verb\*?|Verb\*?|lstinline)(?:\[[^\]\n]*\])?"
    )
    mint_re = re.compile(
        r"\\mintinline\*?(?:\[[^\]\n]*\])?\s*\{[^{}\n]*\}"
    )
    string_re = re.compile(r"\\string\s*\\(?:[A-Za-z@]+|.)")
    literal_argument_re = re.compile(
        r"\\(url|path|nolinkurl|href|hyperlink|hypertarget)(?![A-Za-z@])"
    )

    # Scan in TeX lexical order.  In particular, a percent sign inside
    # \verb, \Verb, minted inline code, a URL, or a literal environment is
    # data and must not start a source comment that hides later live content.
    index = 0
    while index < size:
        if text[index] == "%" and not _escaped(text, index):
            end = text.find("\n", index)
            end = size if end < 0 else end
            blank(index, end)
            index = end
            continue
        if text[index] == "\\" and _escaped(text, index):
            # This slash is part of an escaped/control-symbol sequence (for
            # example ``\\\\includesvg`` typesets a line break plus text).
            # Blank it in the scan view so it cannot become a false command
            # start while leaving any later genuinely active odd slash intact.
            chars[index] = " "
            index += 1
            continue
        if text[index] != "\\":
            index += 1
            continue

        match = env_re.match(text, index)
        if match is not None:
            end_re = re.compile(
                r"\\end\s*\{" + re.escape(match.group(1)) + r"\}"
            )
            close = end_re.search(text, match.end())
            end = close.end() if close else size
            blank(index, end)
            index = end
            continue

        match = literal_re.match(text, index)
        if match is not None:
            pos = match.end()
            if pos < size and not text[pos].isspace():
                delimiter = text[pos]
                closing = text.find(delimiter, pos + 1)
                end = size if closing < 0 else closing + 1
                blank(index, end)
                index = end
                continue

        match = mint_re.match(text, index)
        if match is not None:
            pos = match.end()
            while pos < size and text[pos].isspace():
                pos += 1
            if pos >= size:
                blank(index, size)
                break
            if text[pos] == "{":
                end = find_matching(text, pos, "{", "}")
            else:
                delimiter = text[pos]
                closing = text.find(delimiter, pos + 1)
                end = size if closing < 0 else closing + 1
            end = end if end is not None else size
            blank(index, end)
            index = end
            continue

        match = string_re.match(text, index)
        if match is not None:
            blank(index, match.end())
            index = match.end()
            continue

        match = literal_argument_re.match(text, index)
        if match is not None:
            pos = match.end()
            while pos < size and text[pos].isspace():
                pos += 1
            if pos < size and text[pos] == "{":
                end = find_matching(text, pos, "{", "}")
                if end is not None:
                    blank(pos + 1, end - 1)
                    index = end
                    continue
            elif match.group(1) in {"url", "path", "nolinkurl"} and pos < size:
                delimiter = text[pos]
                closing = text.find(delimiter, pos + 1)
                end = size if closing < 0 else closing + 1
                blank(index, end)
                index = end
                continue

        index += 1

    return "".join(chars)


def _visual_definition_matches(masked: str) -> list[VisualDefinitionMatch]:
    """Parse common definitions without interpreting their bodies as output."""

    definitions: list[VisualDefinitionMatch] = []

    def control_or_group_bounds(cursor: int) -> tuple[int, int] | None:
        cursor = _skip_space(masked, cursor)
        if cursor < len(masked) and masked[cursor] == "{":
            end = find_matching(masked, cursor, "{", "}")
            return (cursor, end) if end is not None else None
        match = re.match(r"\\(?:[A-Za-z@]+|.)", masked[cursor:])
        return (cursor, cursor + len(match.group(0))) if match else None

    def group_bounds(cursor: int, opener: str = "{", closer: str = "}") -> tuple[int, int] | None:
        cursor = _skip_space(masked, cursor)
        end = find_matching(masked, cursor, opener, closer)
        return (cursor, end) if end is not None else None

    for name in (
        "newcommand",
        "renewcommand",
        "providecommand",
        "DeclareRobustCommand",
    ):
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            cursor = _skip_space(masked, match.end())
            if masked.startswith("<>", cursor):
                cursor = _skip_space(masked, cursor + 2)
            if cursor < len(masked) and masked[cursor] == "*":
                cursor = _skip_space(masked, cursor + 1)
            target = control_or_group_bounds(cursor)
            if target is None:
                continue
            cursor = target[1]
            for _ in range(2):
                optional = group_bounds(cursor, "[", "]")
                if optional is None:
                    break
                cursor = optional[1]
            body = group_bounds(cursor)
            if body is not None:
                definitions.append(
                    VisualDefinitionMatch(
                        match.start(),
                        body[1],
                        ((body[0] + 1, body[1] - 1),),
                    )
                )

    for name in ("newenvironment", "renewenvironment"):
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            environment_name = group_bounds(match.end())
            if environment_name is None:
                continue
            cursor = environment_name[1]
            for _ in range(2):
                optional = group_bounds(cursor, "[", "]")
                if optional is None:
                    break
                cursor = optional[1]
            bodies: list[tuple[int, int]] = []
            for _ in range(2):
                body = group_bounds(cursor)
                if body is None:
                    break
                bodies.append((body[0] + 1, body[1] - 1))
                cursor = body[1]
            if len(bodies) == 2:
                definitions.append(
                    VisualDefinitionMatch(match.start(), cursor, tuple(bodies))
                )

    for name in (
        "NewDocumentCommand",
        "RenewDocumentCommand",
        "ProvideDocumentCommand",
        "DeclareDocumentCommand",
        "NewExpandableDocumentCommand",
    ):
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            target = control_or_group_bounds(match.end())
            if target is None:
                continue
            signature = group_bounds(target[1])
            body = group_bounds(signature[1]) if signature is not None else None
            if body is not None:
                definitions.append(
                    VisualDefinitionMatch(
                        match.start(),
                        body[1],
                        ((body[0] + 1, body[1] - 1),),
                    )
                )

    for name in (
        "NewDocumentEnvironment",
        "RenewDocumentEnvironment",
        "ProvideDocumentEnvironment",
    ):
        for match in re.finditer(r"\\" + name + r"(?![A-Za-z@])", masked):
            cursor = match.end()
            groups: list[tuple[int, int]] = []
            for _ in range(4):
                group = group_bounds(cursor)
                if group is None:
                    break
                groups.append(group)
                cursor = group[1]
            if len(groups) == 4:
                definitions.append(
                    VisualDefinitionMatch(
                        match.start(),
                        cursor,
                        tuple((start + 1, end - 1) for start, end in groups[-2:]),
                    )
                )

    for match in re.finditer(r"\\(?:def|gdef|edef|xdef)(?![A-Za-z@])", masked):
        body_start = masked.find("{", match.end())
        if body_start < 0:
            continue
        body_end = find_matching(masked, body_start, "{", "}")
        if body_end is not None:
            definitions.append(
                VisualDefinitionMatch(
                    match.start(),
                    body_end,
                    ((body_start + 1, body_end - 1),),
                )
            )

    for match in re.finditer(r"\\(?:let|futurelet)(?![A-Za-z@])", masked):
        target = control_or_group_bounds(match.end())
        if target is None:
            continue
        cursor = _skip_space(masked, target[1])
        if cursor < len(masked) and masked[cursor] == "=":
            cursor = _skip_space(masked, cursor + 1)
        source = control_or_group_bounds(cursor)
        if source is not None:
            definitions.append(
                VisualDefinitionMatch(
                    match.start(),
                    source[1],
                    (source,),
                )
            )

    return sorted(definitions, key=lambda item: (item.start, item.end))


def _mask_visual_definition_regions(masked: str) -> str:
    """Blank definitions so visual instrumentation never edits a macro body."""

    chars = list(masked)
    for definition in _visual_definition_matches(masked):
        for index in range(definition.start, min(definition.end, len(chars))):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def _definition_identifier(
    masked: str,
    definition: VisualDefinitionMatch,
) -> str | None:
    """Return a stable command/environment name for a parsed definition."""

    header_end = min(
        (start for start, _ in definition.content_ranges),
        default=definition.end,
    )
    header = masked[definition.start:header_end]
    environment = re.match(
        r"\\(?:newenvironment|renewenvironment|"
        r"NewDocumentEnvironment|RenewDocumentEnvironment|"
        r"ProvideDocumentEnvironment)\s*\{\s*([^{}\s]+)\s*\}",
        header,
    )
    if environment is not None:
        return "environment:" + environment.group(1)
    command = re.match(
        r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|"
        r"NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand|"
        r"DeclareDocumentCommand|NewExpandableDocumentCommand|"
        r"def|gdef|edef|xdef|let|futurelet)"
        r"(?:\s*<>)?\s*\*?\s*(?:\{\s*)?\\([A-Za-z@]+)",
        header,
    )
    return "command:" + command.group(1) if command is not None else None


def reachable_visual_definitions(sources: list[str]) -> set[str]:
    """Resolve the statically named definition call graph across a project."""

    defined_calls: dict[str, set[str]] = {}
    direct_calls: set[str] = set()
    dynamic = False
    for source in sources:
        active = _active_visual_lexical_mask(source)
        definitions = _visual_definition_matches(active)
        nondefinitions = _mask_visual_definition_regions(active)
        if re.search(r"\\csname(?![A-Za-z@])", nondefinitions):
            dynamic = True
        direct_calls.update(
            "command:" + match.group(1)
            for match in re.finditer(r"\\([A-Za-z@]+)", nondefinitions)
        )
        direct_calls.update(
            "environment:" + match.group(1).strip()
            for match in re.finditer(r"\\begin\s*\{([^{}]+)\}", nondefinitions)
        )
        for definition in definitions:
            identifier = _definition_identifier(active, definition)
            if identifier is None:
                continue
            calls = defined_calls.setdefault(identifier, set())
            for start, end in definition.content_ranges:
                body = _mask_visual_definition_regions(active[start:end])
                if re.search(r"\\csname(?![A-Za-z@])", body):
                    dynamic = True
                calls.update(
                    "command:" + match.group(1)
                    for match in re.finditer(r"\\([A-Za-z@]+)", body)
                )
                calls.update(
                    "environment:" + match.group(1).strip()
                    for match in re.finditer(r"\\begin\s*\{([^{}]+)\}", body)
                )

    if dynamic:
        return set(defined_calls)
    reachable = set(defined_calls).intersection(direct_calls)
    while True:
        expanded = reachable | {
            called
            for identifier in reachable
            for called in defined_calls.get(identifier, set())
            if called in defined_calls
        }
        if expanded == reachable:
            return reachable
        reachable = expanded


def _active_visual_lexical_mask(text: str) -> str:
    """Mask literals and unreachable branches while retaining definitions."""

    lexical = mask_tex(text)
    conditional_tokens = _mask_visual_definition_regions(lexical)
    return reduce_literal_conditionals(lexical, token_mask=conditional_tokens)


def _visual_scan_mask(text: str) -> str:
    """Return the active, nonliteral, non-definition view used for visuals."""

    return _mask_visual_definition_regions(_active_visual_lexical_mask(text))


def reduce_literal_conditionals(text: str, token_mask: str | None = None) -> str:
    r"""Remove statically inactive ``\iftrue``/``\iffalse`` branches.

    The replacement preserves byte offsets and line breaks.  Unknown TeX
    conditionals remain intact and conservative; only literal truth values are
    reduced.
    """

    masked = token_mask if token_mask is not None else mask_tex(text)
    if len(masked) != len(text):
        raise ValueError("Conditional token mask must preserve source length")
    masked_chars = list(masked)
    for match in re.finditer(r"\\string\s*\\(?:[A-Za-z@]+|.)", masked):
        for index in range(match.start(), match.end()):
            if masked_chars[index] not in "\r\n":
                masked_chars[index] = " "
    masked = "".join(masked_chars)
    chars = list(text)
    stack: list[bool | None] = []
    cursor = 0

    def inactive() -> bool:
        return any(state is False for state in stack)

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    token_pattern = re.compile(
        r"\\if(?:[A-Za-z@]+)?(?![A-Za-z@])|\\else(?![A-Za-z@])|\\fi(?![A-Za-z@])"
    )
    for match in token_pattern.finditer(masked):
        if inactive():
            blank(cursor, match.start())
        token = match.group(0)
        was_inactive = inactive()
        if token.startswith(r"\if"):
            declaration_prefix = masked[max(0, match.start() - 128) : match.start()]
            if re.search(r"\\newif\s*$", declaration_prefix):
                if was_inactive:
                    blank(match.start(), match.end())
                cursor = match.end()
                continue
            if token == r"\iffalse":
                stack.append(False)
                blank(match.start(), match.end())
            elif token == r"\iftrue":
                stack.append(True)
                blank(match.start(), match.end())
            else:
                stack.append(None)
                if was_inactive:
                    blank(match.start(), match.end())
        elif token == r"\else":
            if stack and stack[-1] is not None:
                stack[-1] = not stack[-1]
                blank(match.start(), match.end())
            elif was_inactive:
                blank(match.start(), match.end())
        else:  # \fi
            state = stack.pop() if stack else None
            if state is not None or was_inactive:
                blank(match.start(), match.end())
        cursor = match.end()
    if inactive():
        blank(cursor, len(text))
    return "".join(chars)


def _skip_space(masked: str, index: int) -> int:
    while index < len(masked) and masked[index].isspace():
        index += 1
    return index


def find_matching(masked: str, start: int, opener: str, closer: str) -> int | None:
    """Return the exclusive end of one balanced group."""

    if start >= len(masked) or masked[start] != opener:
        return None
    closers = [closer]
    for index in range(start + 1, len(masked)):
        if _escaped(masked, index):
            continue
        char = masked[index]
        if char == "{":
            closers.append("}")
        elif closers[-1] != "}" and char == opener:
            closers.append(closer)
        elif char == closers[-1]:
            closers.pop()
            if not closers:
                return index + 1
    return None


def iter_commands(text: str, name: str) -> list[CommandMatch]:
    """Find active commands with at most one optional and one required argument."""

    masked = mask_tex(text)
    pattern = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
    matches: list[CommandMatch] = []
    for found in pattern.finditer(masked):
        cursor = found.end()
        starred = cursor < len(masked) and masked[cursor] == "*"
        if starred:
            cursor += 1
        cursor = _skip_space(masked, cursor)
        optional = None
        if cursor < len(masked) and masked[cursor] == "[":
            end = find_matching(masked, cursor, "[", "]")
            if end is None:
                continue
            optional = (cursor, end)
            cursor = _skip_space(masked, end)
        mandatory = None
        if cursor < len(masked) and masked[cursor] == "{":
            end = find_matching(masked, cursor, "{", "}")
            if end is not None:
                mandatory = (cursor, end)
                cursor = end
        matches.append(
            CommandMatch(found.start(), cursor, name, optional, mandatory, starred)
        )
    return matches


def iter_graphic_commands(text: str) -> list[GraphicCommandMatch]:
    r"""Parse ``\includegraphics`` including ltx-talk/Beamer overlay syntax."""

    masked = _visual_scan_mask(text)
    covered_ranges = _owned_visual_suppressing_ranges(text)
    matches: list[GraphicCommandMatch] = []
    for found in re.finditer(r"\\includegraphics(?![A-Za-z@])", masked):
        if any(start < found.start() < end for start, end in covered_ranges):
            continue
        cursor = found.end()
        starred = cursor < len(masked) and masked[cursor] == "*"
        if starred:
            cursor += 1
        cursor = _skip_space(masked, cursor)
        overlay = None
        if cursor < len(masked) and masked[cursor] == "<":
            end = find_matching(masked, cursor, "<", ">")
            if end is None:
                continue
            overlay = (cursor, end)
            cursor = _skip_space(masked, end)
        option_insert = cursor
        optionals: list[tuple[int, int]] = []
        for _ in range(2):
            if cursor >= len(masked) or masked[cursor] != "[":
                break
            end = find_matching(masked, cursor, "[", "]")
            if end is None:
                break
            optionals.append((cursor, end))
            cursor = _skip_space(masked, end)
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        end = find_matching(masked, cursor, "{", "}")
        if end is None:
            continue
        matches.append(
            GraphicCommandMatch(
                found.start(),
                end,
                starred,
                overlay,
                tuple(optionals),
                (cursor, end),
                option_insert,
            )
        )
    return matches


def group_content(text: str, group: tuple[int, int] | None) -> str | None:
    if group is None:
        return None
    return text[group[0] + 1 : group[1] - 1]


def apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping source edits from right to left."""

    last_start = len(text) + 1
    for start, end, replacement in sorted(replacements, reverse=True):
        if end > last_start:
            raise ValueError("Overlapping TeX transformations")
        text = text[:start] + replacement + text[end:]
        last_start = start
    return text


_STANDARD_TAGGED_THEOREM_ENVIRONMENTS = frozenset(
    {
        "assumption",
        "claim",
        "conjecture",
        "corollary",
        "definition",
        "example",
        "exercise",
        "fact",
        "lemma",
        "proof",
        "problem",
        "proposition",
        "remark",
        "theorem",
    }
)


def tagged_theorem_environment_names(sources: list[str]) -> set[str]:
    """Return common and statically declared theorem environment names."""

    names = set(_STANDARD_TAGGED_THEOREM_ENVIRONMENTS)
    pattern = re.compile(
        r"\\newtheorem(?![A-Za-z@])\s*\*?\s*\{\s*([^{}\s]+)\s*\}"
    )
    for source in sources:
        active = _visual_scan_mask(source)
        names.update(match.group(1) for match in pattern.finditer(active))
    return names


def transform_theorem_note_citations(
    text: str,
    relative: Path,
    theorem_environments: set[str] | None = None,
) -> tuple[str, list[Finding]]:
    """Shield optional citations from tagged-amsthm's theorem-note parser."""

    environments = tagged_theorem_environment_names([text])
    environments.update(theorem_environments or ())
    masked = _visual_scan_mask(text)
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []

    for begin in re.finditer(r"\\begin(?![A-Za-z@])", masked):
        cursor = _skip_space(masked, begin.end())
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        environment_end = find_matching(masked, cursor, "{", "}")
        if environment_end is None:
            continue
        environment = text[cursor + 1 : environment_end - 1].strip()
        if environment not in environments:
            continue

        cursor = _skip_space(masked, environment_end)
        if cursor < len(masked) and masked[cursor] == "<":
            overlay_end = find_matching(masked, cursor, "<", ">")
            if overlay_end is None:
                continue
            cursor = _skip_space(masked, overlay_end)
        if cursor >= len(masked) or masked[cursor] != "[":
            continue
        option_end = find_matching(masked, cursor, "[", "]")
        if option_end is None:
            continue

        option = text[cursor + 1 : option_end - 1]
        wrapper = iter_commands(option, "CanvasAllyTheoremNote")
        if wrapper:
            first = wrapper[0]
            if (
                not option[: first.start].strip()
                and first.mandatory is not None
                and not option[first.end :].strip()
            ):
                continue
        if not any(
            citation.optional is not None and citation.mandatory is not None
            for citation in iter_commands(option, "cite")
        ):
            continue

        replacements.append(
            (
                cursor + 1,
                option_end - 1,
                r"\CanvasAllyTheoremNote{" + option + "}",
            )
        )
        findings.append(
            Finding(
                "tagged-theorem-citation-wrapped",
                f"Protected a citation in the optional {environment} heading from a tagged-LaTeX parsing failure.",
                "info",
                str(relative),
                line_number(text, begin.start()),
            )
        )

    return apply_replacements(text, replacements), findings


def split_top_level(value: str) -> list[str]:
    """Split comma-separated TeX key/value text outside groups."""

    result: list[str] = []
    start = 0
    closers: list[str] = []
    matching = {"{": "}", "[": "]", "(": ")"}
    for index, char in enumerate(value):
        if _escaped(value, index):
            continue
        if char == "{":
            closers.append("}")
        elif closers and char == closers[-1]:
            closers.pop()
        elif (not closers or closers[-1] != "}") and char in {"[", "("}:
            closers.append(matching[char])
        elif char == "," and not closers:
            result.append(value[start:index].strip())
            start = index + 1
    result.append(value[start:].strip())
    return [item for item in result if item]


def latex_to_plain(value: str) -> str:
    """Make a conservative plain-text fallback from a short LaTeX caption/title."""

    value = re.sub(r"\\texorpdfstring\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\2", value)
    value = re.sub(r"\\(?:label|cite|ref|eqref|footnote)\s*\{[^{}]*\}", "", value)
    value = re.sub(r"\\(?:textbf|textit|emph|alert|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", value)
    value = value.replace("~", " ")
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def escape_tex_text(value: str) -> str:
    """Escape human-supplied plain text for a macro value."""

    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "%": r"\%",
        "#": r"\#",
        "&": r"\&",
        "_": r"\_",
        "$": r"\$",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def detect_document_class(text: str) -> tuple[str, CommandMatch]:
    matches = iter_commands(text, "documentclass")
    if not matches or matches[0].mandatory is None:
        raise ValueError("No active \\documentclass command found in the main file")
    match = matches[0]
    return (group_content(text, match.mandatory) or "").strip(), match


def _remove_generated_blocks(text: str) -> str:
    pattern = re.compile(
        re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER) + r"\s*",
        re.DOTALL,
    )
    return pattern.sub("", text)


def _metadata_content(existing: str | None, language: str) -> str:
    items = split_top_level(existing or "")
    managed = {"lang", "pdfversion", "pdfstandard", "tagging", "tagging-setup"}
    kept = []
    for item in items:
        key = item.split("=", 1)[0].strip()
        if key not in managed:
            kept.append(item)
    kept.extend(
        [
            f"lang={language}",
            "pdfversion=2.0",
            "pdfstandard=UA-2",
            "tagging=on",
            "tagging-setup={math/setup=mathml-SE,math/alt/use=true}",
        ]
    )
    return ",\n  ".join(kept)


def _class_options_for_ltx_talk(
    options: str | None,
) -> tuple[str, list[str], list[str]]:
    mapped: list[str] = []
    dropped: list[str] = []
    xcolor_options: list[str] = []
    ratios = {
        "169": "16:9",
        "1610": "16:10",
        "2013": "20:13",
        "149": "14:9",
        "141": "141:100",
        "54": "5:4",
        "43": "4:3",
        "32": "3:2",
    }
    aspect_ratio = "4:3"  # Beamer's default; ltx-talk otherwise defaults to 16:9.
    for option in split_top_level(options or ""):
        stripped = option.strip()
        if stripped.startswith("aspectratio="):
            raw = stripped.split("=", 1)[1].strip()
            if raw in ratios:
                aspect_ratio = ratios[raw]
            elif raw.isdigit() and 2 <= len(raw) <= 4:
                split_at = len(raw) // 2 if len(raw) % 2 == 0 else len(raw) - 1
                numerator, denominator = raw[:split_at], raw[split_at:]
                if int(numerator) > 0 and int(denominator) > 0:
                    aspect_ratio = f"{int(numerator)}:{int(denominator)}"
                else:
                    dropped.append(stripped)
            else:
                dropped.append(stripped)
        elif re.fullmatch(r"(?:8|9|10|11|12|14|17|20)pt", stripped):
            mapped.append(f"font-size={stripped}")
        elif stripped == "handout":
            mapped.append("mode=handout")
        elif stripped.startswith("xcolor="):
            raw = stripped.split("=", 1)[1].strip()
            if raw.startswith("{") and find_matching(raw, 0, "{", "}") == len(raw):
                raw = raw[1:-1].strip()
            if raw:
                xcolor_options.append(raw)
            else:
                dropped.append(stripped)
        else:
            dropped.append(stripped)
    mapped.insert(0, f"aspect-ratio={aspect_ratio}")
    return (f"[{','.join(mapped)}]" if mapped else ""), dropped, xcolor_options


def _map_overlay_mode_spec(
    specification: str,
    target_mode: str = "projector",
) -> tuple[str, bool]:
    """Translate Beamer-only mode names inside one ``<...>`` specification."""

    if not (specification.startswith("<") and specification.endswith(">")):
        return specification, False
    changed = False
    mapped_parts: list[str] = []
    for part in specification[1:-1].split("|"):
        stripped = part.strip()
        match = re.fullmatch(
            r"(projector|handout|beamer|presentation|article|trans|second)(?:\s*:\s*(.*))?",
            stripped,
        )
        if match is None:
            mapped_parts.append(part)
            continue
        mode, action = match.groups()
        changed = True
        if mode in {"beamer", "projector", "handout"}:
            normalized_mode = "projector" if mode == "beamer" else mode
            if normalized_mode == target_mode:
                mapped_parts.append(action if action is not None else "all")
            else:
                mapped_parts.append("0")
        elif mode == "presentation":
            # Beamer's presentation umbrella includes both projector and
            # handout output. An unqualified ltx-talk action does the same.
            mapped_parts.append(action if action is not None else "all")
        else:
            # article/trans/second material is not part of this static slide
            # copy. Overlay zero is deliberately never selected.
            mapped_parts.append("0")
    nonzero = [part for part in mapped_parts if part.strip() != "0"]
    return "<" + "|".join(nonzero or ["0"]) + ">", changed


def _map_beamer_overlay_modes(
    text: str,
    relative: Path,
    target_mode: str,
) -> tuple[str, list[Finding]]:
    """Map active Beamer mode-qualified overlays to ltx-talk semantics."""

    masked = mask_tex(text)
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for match in re.finditer(r"<[^<>\r\n]*>", masked):
        prefix = masked[: match.start()]
        if re.search(
            r"(?:\\[A-Za-z@]+[+*]?|\\begin\s*\{[A-Za-z@*]+\})\s*$",
            prefix,
        ) is None:
            continue
        mapped, changed = _map_overlay_mode_spec(
            text[match.start() : match.end()],
            target_mode,
        )
        if not changed:
            continue
        replacements.append((match.start(), match.end(), mapped))
        findings.append(
            Finding(
                "beamer-overlay-mode-mapped",
                f"Mapped Beamer overlay mode {text[match.start():match.end()]!r} to {mapped!r}.",
                "info",
                str(relative),
                line_number(text, match.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def _map_onslide_modifiers(
    text: str,
    relative: Path,
) -> tuple[str, list[Finding]]:
    """Translate Beamer's plus/star onslide variants to supported commands."""

    masked = mask_tex(text)
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for match in re.finditer(r"\\onslide([+*])(?=\s*<)", masked):
        overlay_start = _skip_space(masked, match.end())
        overlay_end = find_matching(masked, overlay_start, "<", ">")
        body_start = _skip_space(masked, overlay_end) if overlay_end is not None else len(masked)
        if overlay_end is None or body_start >= len(masked) or masked[body_start] != "{":
            findings.append(
                Finding(
                    "unsupported-beamer-stateful-onslide",
                    f"Unbraced \\onslide{match.group(1)} has stateful semantics that cannot be migrated safely; wrap its content in braces.",
                    "error",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
            continue
        command = r"\visible" if match.group(1) == "+" else r"\only"
        replacements.append((match.start(), match.end(), command))
        findings.append(
            Finding(
                "beamer-onslide-modifier-mapped",
                f"Mapped \\onslide{match.group(1)} to {command}.",
                "info",
                str(relative),
                line_number(text, match.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def _rewrite_stateful_beamer_modes(
    text: str,
    relative: Path,
    target_mode: str,
) -> tuple[str, list[Finding]]:
    """Convert Beamer's declaration-form mode switches to scoped content."""

    masked = mask_tex(text)
    candidates = list(re.finditer(r"\\mode\s*<([^<>\r\n]+)>", masked))
    switches: list[re.Match[str]] = []
    for match in candidates:
        cursor = _skip_space(masked, match.end())
        if cursor >= len(masked) or masked[cursor] != "{":
            switches.append(match)
    if not switches:
        return text, []

    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    document_end = re.search(r"\\end\s*\{document\}", masked)
    for index, match in enumerate(switches):
        end = switches[index + 1].start() if index + 1 < len(switches) else len(text)
        scope_end = min(end, document_end.start()) if document_end else end
        mode = match.group(1).strip()
        region = text[match.end() : scope_end]
        suffix = text[scope_end:end]
        if mode in {"all", "presentation"}:
            replacement = _blank_preserving_lines(text, match.start(), match.end()) + region + suffix
        elif mode in {"article", "trans", "second"}:
            replacement = _blank_preserving_lines(text, match.start(), scope_end) + suffix
        elif mode in {"beamer", "projector", "handout"}:
            normalized_mode = "projector" if mode == "beamer" else mode
            if normalized_mode == target_mode:
                replacement = _blank_preserving_lines(text, match.start(), match.end()) + region + suffix
            else:
                replacement = _blank_preserving_lines(text, match.start(), scope_end) + suffix
        else:
            findings.append(
                Finding(
                    "unsupported-beamer-stateful-mode",
                    f"Stateful Beamer mode {mode!r} cannot be migrated safely; use a braced \\mode command.",
                    "error",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
            continue
        replacements.append((match.start(), end, replacement))
        findings.append(
            Finding(
                "beamer-stateful-mode-mapped",
                f"Converted stateful Beamer mode {mode!r} to scoped static-slide content.",
                "info",
                str(relative),
                line_number(text, match.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def _reduce_never_selected_branches(
    text: str,
    relative: Path,
) -> tuple[str, list[Finding]]:
    """Keep only the branch selected when an overlay spec is always false."""

    findings: list[Finding] = []
    # ltx-talk renders only the first branch of a statically empty \temporal.
    # Keep the native wrapper and both surrounding branches exactly as written
    # so its page-generation semantics remain unchanged, but blank the
    # unreachable middle content before visual/math transformations inspect it.
    masked = mask_tex(text)
    chars = list(text)
    for match in re.finditer(r"\\temporal(?![A-Za-z@])", masked):
        cursor = _skip_space(masked, match.end())
        if cursor >= len(masked) or masked[cursor] != "<":
            continue
        overlay_end = find_matching(masked, cursor, "<", ">")
        if overlay_end is None or not _literal_overlay_never_selected(
            text[cursor + 1 : overlay_end - 1]
        ):
            continue
        cursor = _skip_space(masked, overlay_end)
        groups: list[tuple[int, int]] = []
        for _ in range(3):
            if cursor >= len(masked) or masked[cursor] != "{":
                break
            end = find_matching(masked, cursor, "{", "}")
            if end is None:
                break
            groups.append((cursor, end))
            cursor = _skip_space(masked, end)
        if len(groups) != 3:
            continue
        middle_start, middle_end = groups[1][0] + 1, groups[1][1] - 1
        if any(not char.isspace() for char in text[middle_start:middle_end]):
            findings.append(
                Finding(
                    "beamer-nonpresentation-content-omitted",
                    "Omitted the unreachable middle branch of a statically empty "
                    "\\temporal while preserving its native wrapper.",
                    "info",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
        for index in range(middle_start, middle_end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    text = "".join(chars)

    # An empty \alt always chooses its second argument, so it can be reduced
    # completely without affecting the static presentation.
    for name, group_count, selected_index in (("alt", 2, 1),):
        pattern = re.compile(
            r"\\" + name + r"(?![A-Za-z@])\s*<([^<>\r\n]*)>"
        )
        while True:
            masked = mask_tex(text)
            candidates = [
                match
                for match in pattern.finditer(masked)
                if _literal_overlay_never_selected(match.group(1))
            ]
            if not candidates:
                break
            match = candidates[-1]
            cursor = _skip_space(masked, match.end())
            groups: list[tuple[int, int]] = []
            for _ in range(group_count):
                if cursor >= len(masked) or masked[cursor] != "{":
                    break
                end = find_matching(masked, cursor, "{", "}")
                if end is None:
                    break
                groups.append((cursor, end))
                cursor = _skip_space(masked, end)
            if len(groups) != group_count:
                break
            keep_start, keep_end = groups[selected_index]
            replacement = (
                _blank_preserving_lines(text, match.start(), keep_start)
                + r"\begingroup "
                + text[keep_start + 1 : keep_end - 1]
                + r" \endgroup"
                + _blank_preserving_lines(text, keep_end, cursor)
            )
            text = apply_replacements(text, [(match.start(), cursor, replacement)])
            findings.append(
                Finding(
                    "beamer-nonpresentation-branch-selected",
                    f"Kept the presentation branch of \\{name} and omitted its never-selected branch(es).",
                    "info",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
    return text, findings


def _literal_overlay_never_selected(specification: str) -> bool:
    """Return true for a complete numeric overlay with no positive slide."""

    overlay = re.sub(r"\s+", "", specification)
    if not overlay:
        return False
    saw_item = False
    for item in overlay.split(","):
        saw_item = True
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
    return saw_item


def _normalize_section_mode_overlays(
    text: str,
    relative: Path,
) -> tuple[str, list[Finding]]:
    """Remove selected Beamer section modes and omit excluded headings."""

    masked = mask_tex(text)
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    pattern = re.compile(
        r"\\(part|section|subsection|subsubsection)(\*)?\s*"
        r"<(all|0|projector|handout)>\s*(\*)?"
    )
    for match in pattern.finditer(masked):
        mode = match.group(3)
        cursor = _skip_space(masked, match.end())
        if cursor < len(masked) and masked[cursor] == "[":
            end = find_matching(masked, cursor, "[", "]")
            if end is None:
                continue
            cursor = _skip_space(masked, end)
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        end = find_matching(masked, cursor, "{", "}")
        if end is None:
            continue
        star = "*" if match.group(2) or match.group(4) else ""
        command = "\\" + match.group(1) + star + text[match.end() : end]
        if mode == "all":
            replacement = command
            detail = "Kept a presentation-mode heading and removed its Beamer mode specifier."
        elif mode == "0":
            replacement = _blank_preserving_lines(text, match.start(), end)
            detail = "Omitted a heading assigned only to a non-presentation Beamer mode."
        else:
            replacement = rf"\mode<{mode}>{{" + command + "}"
            detail = f"Scoped a {mode}-mode heading for ltx-talk."
        replacements.append((match.start(), end, replacement))
        findings.append(
            Finding(
                "beamer-section-mode-normalized",
                detail,
                "info",
                str(relative),
                line_number(text, match.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def _command_body_end(masked: str, start: int, name: str) -> int | None:
    """Return the end of a command with optional ``<>``, ``[]``, then ``{}``."""

    cursor = _skip_space(masked, start + len(name) + 1)
    for opener, closer in (("<", ">"), ("[", "]")):
        if cursor < len(masked) and masked[cursor] == opener:
            end = find_matching(masked, cursor, opener, closer)
            if end is None:
                return None
            cursor = _skip_space(masked, end)
    if cursor >= len(masked) or masked[cursor] != "{":
        return None
    return find_matching(masked, cursor, "{", "}")


def _command_signature_end(
    masked: str,
    start: int,
    name: str,
    signature: tuple[str, ...],
) -> int | None:
    """Consume a small xparse-style signature without swallowing later groups."""

    cursor = start + len(name) + 1
    groups = {
        "d": ("<", ">", False),
        "o": ("[", "]", False),
        "m": ("{", "}", True),
        "g": ("{", "}", False),
    }
    for argument in signature:
        cursor = _skip_space(masked, cursor)
        if argument == "s":
            if cursor < len(masked) and masked[cursor] == "*":
                cursor += 1
            continue
        opener, closer, required = groups[argument]
        if cursor >= len(masked) or masked[cursor] != opener:
            if required:
                return None
            continue
        end = find_matching(masked, cursor, opener, closer)
        if end is None:
            return None
        cursor = end
    return cursor


def _blank_preserving_lines(text: str, start: int, end: int) -> str:
    """Remove TeX tokens while keeping offsets and source line numbers stable."""

    return "".join(char if char in "\r\n" else " " for char in text[start:end])


def _omit_nonrendered_beamer_content(
    text: str,
    relative: Path,
) -> tuple[str, list[Finding]]:
    """Remove content known not to appear in the migrated static slide copy."""

    findings: list[Finding] = []
    # Remove disabled frame/environment regions first so nested notes or theme
    # commands cannot produce overlapping edits or irrelevant warnings.
    for environment in (
        "frame",
        "onlyenv",
        "visibleenv",
        "uncoverenv",
        "actionenv",
        "block",
        "alertblock",
        "exampleblock",
        "theorem",
        "definition",
        "example",
    ):
        masked = mask_tex(text)
        replacements: list[tuple[int, int, str]] = []
        event_pattern = re.compile(
            r"\\(begin|end)\s*\{"
            + environment
            + r"\}(?:\s*<([^<>\r\n]*)>)?"
        )
        stack: list[tuple[re.Match[str], bool]] = []
        ranges: list[tuple[int, int, re.Match[str]]] = []
        for event in event_pattern.finditer(masked):
            if event.group(1) == "begin":
                stack.append(
                    (
                        event,
                        event.group(2) is not None
                        and _literal_overlay_never_selected(event.group(2)),
                    )
                )
                continue
            if not stack:
                continue
            opening, disabled = stack.pop()
            if disabled:
                ranges.append((opening.start(), event.end(), opening))
        outer_ranges: list[tuple[int, int, re.Match[str]]] = []
        for start, end, opening in sorted(ranges, key=lambda item: (item[0], -item[1])):
            if any(parent_start <= start and end <= parent_end for parent_start, parent_end, _ in outer_ranges):
                continue
            outer_ranges.append((start, end, opening))
        for start, end, opening in outer_ranges:
            replacements.append(
                (
                    start,
                    end,
                    _blank_preserving_lines(text, start, end),
                )
            )
            findings.append(
                Finding(
                    "beamer-nonpresentation-frame-omitted"
                    if environment == "frame"
                    else "beamer-nonpresentation-environment-omitted",
                    "Omitted a frame assigned only to a non-presentation Beamer mode."
                    if environment == "frame"
                    else f"Omitted never-selected {environment} content from the static slide copy.",
                    "info",
                    str(relative),
                    line_number(text, opening.start()),
                )
            )
        text = apply_replacements(text, replacements)

    discarded_commands = {
        "AtBeginSection": (("o", "m"), True),
        "AtBeginSubsection": (("o", "m"), True),
        "AtBeginPart": (("o", "m"), True),
        "AtBeginLecture": (("o", "m"), True),
        "AtBeginNote": (("m",), True),
        "AtEndNote": (("m",), True),
        "logo": (("m",), True),
        "titlegraphic": (("m",), True),
        "setbeamertemplate": (("m", "o", "g"), False),
        "defbeamertemplate": (("d", "s", "m", "m", "o", "o", "m", "o", "g"), False),
        "defbeamertemplateparent": (("m", "o", "m", "o", "o", "m"), False),
        "addtobeamertemplate": (("m", "m", "m"), False),
        "usetheme": (("o", "m"), False),
        "usecolortheme": (("o", "m"), False),
        "usefonttheme": (("o", "m"), False),
        "useinnertheme": (("o", "m"), False),
        "useoutertheme": (("o", "m"), False),
        "setbeamercolor": (("s", "m", "m"), False),
        "setbeamerfont": (("s", "m", "m"), False),
        "setbeamercovered": (("m",), False),
        "setbeamersize": (("m",), False),
        "setbeameroption": (("m",), False),
        "beamerdefaultoverlayspecification": (("m",), True),
        "beamertemplatenavigationsymbolsempty": ((), False),
        "includeonlylecture": (("m",), False),
        "lecture": (("o", "m", "m"), True),
        "note": (("d", "o", "m"), True),
    }
    # Process command types separately so a removed outer hook/configuration
    # cannot overlap a nested command replacement.
    for name, (signature, warn) in discarded_commands.items():
        masked = mask_tex(text)
        replacements: list[tuple[int, int, str]] = []
        for match in re.finditer(r"\\" + re.escape(name) + r"(?![A-Za-z@])", masked):
            end = _command_signature_end(masked, match.start(), name, signature)
            if end is None:
                continue
            replacements.append(
                (match.start(), end, _blank_preserving_lines(text, match.start(), end))
            )
            if name == "note":
                code = "beamer-note-omitted"
                message = "Speaker note omitted from the static accessible slide copy."
            else:
                code = "beamer-preamble-content-omitted"
                message = f"Omitted visual-only Beamer \\{name} configuration from the static slide copy."
            findings.append(
                Finding(
                    code,
                    message,
                    "warning" if warn else "info",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
        text = apply_replacements(text, replacements)

    zero_overlay_commands = {
        "only": ("d", "m"),
        "mode": ("d", "m"),
        "visible": ("d", "m"),
        "uncover": ("d", "m"),
        "onslide": ("d", "m"),
        "action": ("d", "m"),
        "hyperlink": ("d", "m", "m"),
        "hypertarget": ("d", "m", "m"),
        "includegraphics": ("s", "d", "o", "o", "m"),
        "tikz": ("d", "o", "m"),
    }
    for name, signature in zero_overlay_commands.items():
        masked = mask_tex(text)
        replacements = []
        pattern = re.compile(
            r"\\" + name + r"(?![A-Za-z@])\s*<([^<>\r\n]*)>"
        )
        for match in pattern.finditer(masked):
            if not _literal_overlay_never_selected(match.group(1)):
                continue
            end = _command_signature_end(masked, match.start(), name, signature)
            if end is None:
                continue
            replacements.append(
                (match.start(), end, _blank_preserving_lines(text, match.start(), end))
            )
            findings.append(
                Finding(
                    "beamer-nonpresentation-content-omitted",
                    f"Omitted never-selected \\{name} content from the static slide copy.",
                    "info",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
        text = apply_replacements(text, replacements)

    return text, findings


def _onlyenv_body_is_unsafe(body: str) -> bool:
    """Whether an onlyenv body cannot safely become a braced command argument."""

    protected_names = "|".join(
        re.escape(name)
        for name in sorted(PROTECTED_ENVIRONMENTS, key=len, reverse=True)
    )
    protected_environment = re.compile(
        r"\\begin\s*\{(?:" + protected_names + r")\}"
    )
    protected_inline = re.compile(
        r"\\(?:verb\*?|Verb\*?|lstinline|mintinline\*?)(?![A-Za-z@])"
    )
    index = 0
    while index < len(body):
        if body[index] == "%" and not _escaped(body, index):
            newline = body.find("\n", index)
            index = len(body) if newline < 0 else newline + 1
            continue
        if body[index] != "\\" or _escaped(body, index):
            index += 1
            continue
        environment_match = protected_environment.match(body, index)
        if environment_match is not None:
            return True
        inline_match = protected_inline.match(body, index)
        if inline_match is not None:
            return True
        string_match = re.match(r"\\string\s*\\(?:[A-Za-z@]+|.)", body[index:])
        if string_match is not None:
            index += len(string_match.group(0))
            continue
        literal_argument = re.match(
            r"\\(?:url|path|nolinkurl|detokenize|href|hyperlink|hypertarget)"
            r"(?![A-Za-z@])",
            body[index:],
        )
        if literal_argument is not None:
            cursor = _skip_space(body, index + len(literal_argument.group(0)))
            if cursor < len(body) and body[cursor] == "{":
                end = find_matching(body, cursor, "{", "}")
                if end is not None:
                    index = end
                    continue
        control = re.match(r"\\(?:[A-Za-z@]+|.)", body[index:])
        index += len(control.group(0)) if control is not None else 1

    active = mask_tex(body)
    side_effect = re.compile(
        r"\\(?:pause|onslide|label|hypertarget|stepcounter|refstepcounter|"
        r"addtocounter|setcounter|write|global|globaldefs|gdef|xdef|edef|def|"
        r"catcode)(?![A-Za-z@])"
    )
    nested_overlay = re.compile(
        r"\\(?:only|alt|temporal|visible|uncover|action|alert|structure|"
        r"includegraphics)(?![A-Za-z@])\*?\s*<"
    )
    overlay_environment = re.compile(
        r"\\begin\s*\{(?:onlyenv|visibleenv|uncoverenv|actionenv|alertenv)\}"
    )
    return bool(
        side_effect.search(active)
        or nested_overlay.search(active)
        or overlay_environment.search(active)
    )


def _normalize_onlyenv(
    text: str,
    relative: Path,
    *,
    strict: bool,
) -> tuple[str, list[Finding]]:
    """Replace safe onlyenv boxes with scoped only commands to avoid orphan tags."""

    masked = mask_tex(text)
    token = re.compile(r"\\(begin|end)\s*\{onlyenv\}")
    stack: list[re.Match[str]] = []
    pairs: list[tuple[re.Match[str], re.Match[str]]] = []
    for event in token.finditer(masked):
        if event.group(1) == "begin":
            stack.append(event)
        elif stack:
            pairs.append((stack.pop(), event))

    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for opening, closing in pairs:
        cursor = _skip_space(masked, opening.end())
        argument_insert = opening.end()
        if cursor < len(masked) and masked[cursor] == "<":
            overlay_end = find_matching(masked, cursor, "<", ">")
            if overlay_end is None or overlay_end > closing.start():
                findings.append(
                    Finding(
                        "unsafe-onlyenv-tagging",
                        "Could not safely normalize onlyenv; inspect this overlay's "
                        "tag tree manually.",
                        "error" if strict else "warning",
                        str(relative),
                        line_number(text, opening.start()),
                    )
                )
                continue
            argument_insert = overlay_end
        body = text[argument_insert:closing.start()]
        if _onlyenv_body_is_unsafe(body):
            findings.append(
                Finding(
                    "unsafe-onlyenv-tagging",
                    "Kept onlyenv because its body contains verbatim-like, nested "
                    "overlay, or state-changing content that is unsafe in a braced "
                    "\\only command; inspect its tag tree manually.",
                    "error" if strict else "warning",
                    str(relative),
                    line_number(text, opening.start()),
                )
            )
            continue
        replacements.extend(
            (
                (opening.start(), opening.end(), r"\begingroup\only"),
                (argument_insert, argument_insert, "{"),
                (closing.start(), closing.end(), r"}\endgroup"),
            )
        )
        findings.append(
            Finding(
                "onlyenv-normalized",
                "Converted onlyenv to a scoped \\only command so hidden content "
                "cannot leave orphan structure tags.",
                "info",
                str(relative),
                line_number(text, opening.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def _top_level_alignment_tabs(masked_body: str) -> list[int]:
    """Return active alignment-tab offsets outside nested brace groups."""

    tabs: list[int] = []
    brace_depth = 0
    for index, char in enumerate(masked_body):
        if _escaped(masked_body, index):
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "&" and brace_depth == 0:
            tabs.append(index)
    return tabs


def _alignment_action_continuation_overlay(overlay: str) -> str | None:
    """Reuse one relative overlay step without advancing later cells again."""

    compact = re.sub(r"\s+", "", overlay)
    if "\\" in compact:
        return None
    step = re.compile(r"\+(?:\([+-]?[0-9]+\))?(?=-|,|\||$)")
    matches = list(step.finditer(compact))
    if len(matches) > 1:
        return None
    if not matches:
        return compact
    match = matches[0]
    replacement = "." + match.group(0)[1:]
    return compact[: match.start()] + replacement + compact[match.end() :]


def _migrate_alignment_actions(
    text: str,
    relative: Path,
) -> tuple[str, list[Finding]]:
    r"""Make Beamer ``\action`` wrappers safe across alignment cells.

    ``ltx-talk`` implements ``\action`` with TeX groups around its body. An
    alignment tab ends the current cell before those groups can be closed, so
    an otherwise valid Beamer row fails with an extra-group error. Per-cell
    ``\uncover`` wrappers keep every group inside one cell. The first wrapper
    evaluates a relative overlay and later cells refer to its current step.
    """

    masked = _mask_visual_definition_regions(mask_tex(text))
    alignment_ranges = [
        bounds
        for environment in ACTION_ALIGNMENT_ENVIRONMENTS
        for bounds in _environment_ranges(masked, environment)
    ]
    if not alignment_ranges:
        return text, []

    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for match in re.finditer(r"\\action(?![A-Za-z@])", masked):
        if not any(start < match.start() < end for start, end in alignment_ranges):
            continue

        cursor = _skip_space(masked, match.end())
        overlay = "all"
        if cursor < len(masked) and masked[cursor] == "<":
            overlay_end = find_matching(masked, cursor, "<", ">")
            if overlay_end is None:
                continue
            overlay = text[cursor + 1 : overlay_end - 1]
            command_tail = text[match.end() : overlay_end]
            body_cursor = overlay_end
        else:
            command_tail = "<all>"
            body_cursor = match.end()

        body_start = _skip_space(masked, body_cursor)
        if body_start >= len(masked) or masked[body_start] != "{":
            continue
        body_end = find_matching(masked, body_start, "{", "}")
        if body_end is None:
            continue
        masked_body = masked[body_start + 1 : body_end - 1]
        tabs = _top_level_alignment_tabs(masked_body)
        if not tabs:
            continue

        line = line_number(text, match.start())
        continuation_overlay = _alignment_action_continuation_overlay(overlay)
        if "@" in overlay or continuation_overlay is None or _onlyenv_body_is_unsafe(
            text[body_start + 1 : body_end - 1]
        ):
            findings.append(
                Finding(
                    "unsupported-beamer-action-alignment",
                    "A Beamer \\action spanning alignment cells also contains a "
                    "named action or nested/state-changing overlay content; split "
                    "the effect between cells before migration.",
                    "error",
                    str(relative),
                    line,
                )
            )
            continue

        body = text[body_start + 1 : body_end - 1]
        cells: list[str] = []
        previous = 0
        for tab in tabs:
            cells.append(body[previous:tab])
            previous = tab + 1
        cells.append(body[previous:])
        wrapped_cells = [
            r"\uncover"
            + (command_tail if index == 0 else f"<{continuation_overlay}>")
            + (text[body_cursor:body_start] if index == 0 else "")
            + "{"
            + cell
            + "}"
            for index, cell in enumerate(cells)
        ]
        replacement = "&".join(wrapped_cells)
        replacements.append((match.start(), body_end, replacement))
        findings.append(
            Finding(
                "beamer-action-alignment-mapped",
                "Converted a Beamer \\action spanning alignment cells to "
                "per-cell \\uncover commands for ltx-talk compatibility.",
                "info",
                str(relative),
                line,
            )
        )

    return apply_replacements(text, replacements), findings


def _normalize_standalone_vspace_linebreaks(
    text: str,
    relative: Path,
) -> tuple[str, list[Finding]]:
    r"""Remove invalid ``\\`` after a standalone Beamer ``\vspace`` line."""

    masked = _visual_scan_mask(text)
    frame_ranges = [
        bounds
        for environment in ("frame", "frame*")
        for bounds in _environment_ranges(masked, environment)
    ]
    if not frame_ranges:
        return text, []

    alignment_ranges = [
        bounds
        for environment in (
            *ACTION_ALIGNMENT_ENVIRONMENTS,
            "gather",
            "gather*",
            "multline",
            "multline*",
        )
        for bounds in _environment_ranges(masked, environment)
    ]
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []

    def follows_vertical_boundary(offset: int) -> bool:
        cursor = offset
        while cursor > 0 and masked[cursor - 1].isspace():
            cursor -= 1
        separation = text[cursor:offset]
        if re.search(r"\n[ \t\r]*\n", separation):
            return True
        prefix = masked[:cursor]
        if prefix.endswith(r"\]") or re.search(r"\\par(?![A-Za-z@])$", prefix):
            return True
        environment_end = re.search(
            r"\\(begin|end)\s*\{([^{}]+)\}$",
            prefix,
        )
        if environment_end is None:
            return False
        kind, environment = environment_end.groups()
        if kind == "begin":
            return environment in {"frame", "frame*"}
        return environment in {
            "align",
            "align*",
            "alignat",
            "alignat*",
            "displaymath",
            "equation",
            "equation*",
            "eqnarray",
            "eqnarray*",
            "flalign",
            "flalign*",
            "gather",
            "gather*",
            "multline",
            "multline*",
        }

    for command in re.finditer(r"\\vspace(?![A-Za-z@])\*?", masked):
        if not any(start < command.start() < end for start, end in frame_ranges):
            continue
        if any(start < command.start() < end for start, end in alignment_ranges):
            continue

        group_start = _skip_space(masked, command.end())
        if group_start >= len(masked) or masked[group_start] != "{":
            continue
        group_end = find_matching(masked, group_start, "{", "}")
        if group_end is None or "\n" in masked[command.start() : group_end]:
            continue

        line_start = masked.rfind("\n", 0, command.start()) + 1
        if masked[line_start : command.start()].strip():
            continue
        if not follows_vertical_boundary(command.start()):
            continue
        cursor = group_end
        while cursor < len(masked) and masked[cursor] in " \t":
            cursor += 1
        # The lexical mask blanks the second slash of TeX's ``\\`` control
        # symbol while preserving offsets, so inspect the original bytes here.
        if text[cursor : cursor + 2] != r"\\":
            continue
        replacement_end = cursor + 2
        line_end = masked.find("\n", replacement_end)
        if line_end < 0:
            line_end = len(masked)
        if masked[replacement_end:line_end].strip():
            continue

        replacements.append(
            (
                command.start(),
                replacement_end,
                text[command.start() : group_end],
            )
        )
        findings.append(
            Finding(
                "beamer-vspace-linebreak-normalized",
                "Removed a redundant line break after vertical spacing in "
                "proven vertical mode for ltx-talk compatibility.",
                "info",
                str(relative),
                line_number(text, command.start()),
            )
        )

    return apply_replacements(text, replacements), findings


def _preprocess_beamer_content(
    text: str,
    relative: Path,
    target_mode: str = "projector",
    *,
    strict: bool = False,
) -> tuple[str, list[Finding]]:
    # Preamble hooks/templates are swallowed as a whole before interpreting
    # declaration-form mode switches that might occur inside their bodies.
    text, early_omitted_findings = _omit_nonrendered_beamer_content(text, relative)
    text, onslide_findings = _map_onslide_modifiers(text, relative)
    text, stateful_findings = _rewrite_stateful_beamer_modes(text, relative, target_mode)
    text, mode_findings = _map_beamer_overlay_modes(text, relative, target_mode)
    text, section_findings = _normalize_section_mode_overlays(text, relative)
    text, branch_findings = _reduce_never_selected_branches(text, relative)
    text, omitted_findings = _omit_nonrendered_beamer_content(text, relative)
    text, onlyenv_findings = _normalize_onlyenv(
        text,
        relative,
        strict=strict,
    )
    text, action_alignment_findings = _migrate_alignment_actions(text, relative)
    text, vspace_findings = _normalize_standalone_vspace_linebreaks(text, relative)
    return (
        text,
        early_omitted_findings
        + onslide_findings
        + stateful_findings
        + mode_findings
        + section_findings
        + branch_findings
        + omitted_findings
        + onlyenv_findings
        + action_alignment_findings
        + vspace_findings,
    )


def _underlined_inline_math_ranges(
    masked_body: str,
) -> tuple[list[tuple[int, int]], str | None]:
    """Return movable top-level inline-math ranges from an underline body.

    Splitting an underline closes its TeX group, so math nested inside another
    group or preceded by a top-level control sequence may depend on local font,
    color, or macro state.  Those cases deliberately remain manual review
    items instead of being rewritten speculatively.
    """

    ranges: list[tuple[int, int]] = []
    depth = 0
    index = 0
    size = len(masked_body)
    math_environment = re.compile(
        r"\\begin\s*\{(?:math|displaymath|equation\*?|align\*?|alignat\*?|"
        r"gather\*?|multline\*?|flalign\*?|eqnarray\*?)\}"
    )

    def closing_control(start: int, closing: str) -> int | None:
        cursor = start
        while True:
            found = masked_body.find(closing, cursor)
            if found < 0:
                return None
            if not _escaped(masked_body, found):
                return found + len(closing)
            cursor = found + len(closing)

    while index < size:
        char = masked_body[index]
        if char == "{" and not _escaped(masked_body, index):
            depth += 1
            index += 1
            continue
        if char == "}" and not _escaped(masked_body, index):
            depth = max(0, depth - 1)
            index += 1
            continue
        if char == "$" and not _escaped(masked_body, index):
            if depth:
                return [], "inline math is nested inside another TeX group"
            if index + 1 < size and masked_body[index + 1] == "$":
                return [], "display math occurs inside the underline"
            closing = index + 1
            while closing < size:
                closing = masked_body.find("$", closing)
                if closing < 0:
                    return [], "an inline-math dollar is unbalanced"
                if _escaped(masked_body, closing):
                    closing += 1
                    continue
                if closing + 1 < size and masked_body[closing + 1] == "$":
                    return [], "display math occurs inside the underline"
                break
            ranges.append((index, closing + 1))
            index = closing + 1
            continue
        if char != "\\" or _escaped(masked_body, index):
            index += 1
            continue

        if masked_body.startswith(r"\[", index):
            return [], "display math occurs inside the underline"
        if math_environment.match(masked_body, index) is not None:
            return [], "a math environment occurs inside the underline"
        if masked_body.startswith(r"\(", index):
            if depth:
                return [], "inline math is nested inside another TeX group"
            end = closing_control(index + 2, r"\)")
            if end is None:
                return [], r"an inline \(...\) delimiter is unbalanced"
            ranges.append((index, end))
            index = end
            continue
        ensuremath = re.match(r"\\ensuremath(?![A-Za-z@])", masked_body[index:])
        if ensuremath is not None:
            if depth:
                return [], "inline math is nested inside another TeX group"
            cursor = _skip_space(masked_body, index + ensuremath.end())
            if cursor >= size or masked_body[cursor] != "{":
                return [], r"an \ensuremath argument cannot be parsed safely"
            end = find_matching(masked_body, cursor, "{", "}")
            if end is None:
                return [], r"an \ensuremath argument is unbalanced"
            ranges.append((index, end))
            index = end
            continue
        index += 1

    if not ranges:
        return [], None

    # Any remaining top-level control sequence could establish state that was
    # shared with the math while everything lived in one underline group.
    # Moving that math across the group boundary would not be provably safe.
    nonmath = list(masked_body)
    for start, end in ranges:
        for position in range(start, end):
            if nonmath[position] not in "\r\n":
                nonmath[position] = " "
    nonmath_mask = "".join(nonmath)
    depth = 0
    for position, char in enumerate(nonmath_mask):
        if char == "{" and not _escaped(nonmath_mask, position):
            depth += 1
        elif char == "}" and not _escaped(nonmath_mask, position):
            depth = max(0, depth - 1)
        elif char == "\\" and depth == 0 and not _escaped(nonmath_mask, position):
            return [], "a top-level control sequence may affect the math"
    return ranges, None


def transform_underlined_math(
    text: str,
    relative: Path,
    *,
    strict: bool,
) -> tuple[str, list[Finding]]:
    r"""Move safely parsed inline math outside ``\underline`` groups.

    Current tagged ltx-talk output renders inline math inside ``\underline``
    but exposes it only as ordinary text, without Formula structure.  Text-only
    underline segments retain their decoration while each math segment keeps
    its original source spelling and receives normal tagged-math handling.
    """

    masked = _visual_scan_mask(text)
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for match in re.finditer(r"\\underline(?![A-Za-z@])", masked):
        cursor = _skip_space(masked, match.end())
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        end = find_matching(masked, cursor, "{", "}")
        if end is None:
            continue
        body_start, body_end = cursor + 1, end - 1
        ranges, unsafe_reason = _underlined_inline_math_ranges(
            masked[body_start:body_end]
        )
        if unsafe_reason is not None:
            findings.append(
                Finding(
                    "underlined-math-manual-review",
                    "Kept underlined math unchanged because "
                    + unsafe_reason
                    + "; move each formula outside \\underline manually so it "
                    "retains Formula structure.",
                    "error" if strict else "warning",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
            continue
        if not ranges:
            continue

        body = text[body_start:body_end]
        parts: list[str] = []
        previous = 0
        for start, stop in ranges:
            if start > previous:
                parts.append(r"\underline{" + body[previous:start] + "}")
            parts.append(body[start:stop])
            previous = stop
        if previous < len(body):
            parts.append(r"\underline{" + body[previous:] + "}")
        replacements.append((match.start(), end, "".join(parts)))
        findings.append(
            Finding(
                "underlined-math-separated",
                "Moved top-level inline math outside \\underline so tagged math "
                "remains Formula structure; surrounding text stays underlined, "
                "but the formulas do not, so review the visual/semantic effect.",
                "warning",
                str(relative),
                line_number(text, match.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def _migrate_frames(
    text: str,
    relative: Path,
    findings: list[Finding],
    *,
    strict: bool,
    target_mode: str = "projector",
) -> str:
    """Translate common Beamer frame arguments/options to ltx-talk syntax."""

    masked = mask_tex(text)
    openings = list(re.finditer(r"\\begin\s*\{frame\}", masked))
    closings = list(re.finditer(r"\\end\s*\{frame\}", masked))
    replacements: list[tuple[int, int, str]] = []
    close_cursor = 0
    for opening in openings:
        cursor = _skip_space(masked, opening.end())
        overlay_text = ""
        if cursor < len(masked) and masked[cursor] == "<":
            end = find_matching(masked, cursor, "<", ">")
            if end:
                overlay_text = text[cursor:end]
                cursor = _skip_space(masked, end)

        default_action_spec = ""
        options_text = ""
        if cursor < len(masked) and masked[cursor] == "[":
            end = find_matching(masked, cursor, "[", "]")
            if end:
                first_square = text[cursor + 1 : end - 1].strip()
                cursor = _skip_space(masked, end)
                if first_square.startswith("<") and first_square.endswith(">"):
                    default_action_spec, mode_changed = _map_overlay_mode_spec(
                        first_square,
                        target_mode,
                    )
                    if mode_changed:
                        findings.append(
                            Finding(
                                "beamer-overlay-mode-mapped",
                                f"Mapped Beamer default overlay mode {first_square!r} to {default_action_spec!r}.",
                                "info",
                                str(relative),
                                line_number(text, opening.start()),
                            )
                        )
                    if cursor < len(masked) and masked[cursor] == "[":
                        options_end = find_matching(masked, cursor, "[", "]")
                        if options_end:
                            options_text = text[cursor + 1 : options_end - 1]
                            cursor = _skip_space(masked, options_end)
                else:
                    options_text = first_square

        title_text = ""
        subtitle_text = ""
        if cursor < len(masked) and masked[cursor] == "{":
            end = find_matching(masked, cursor, "{", "}")
            if end:
                title_text = text[cursor + 1 : end - 1]
                cursor = end
                subtitle_cursor = _skip_space(masked, cursor)
                if subtitle_cursor < len(masked) and masked[subtitle_cursor] == "{":
                    subtitle_end = find_matching(masked, subtitle_cursor, "{", "}")
                    if subtitle_end:
                        subtitle_text = text[subtitle_cursor + 1 : subtitle_end - 1]
                        cursor = subtitle_end

        fragile = False
        frame_target = ""
        mapped_options: list[str] = []
        if default_action_spec:
            mapped_options.append("action-spec=" + default_action_spec)
        alignment = {
            "t": "vertical-alignment=top",
            "c": "vertical-alignment=center",
            "b": "vertical-alignment=bottom",
            "s": "vertical-alignment=stretch",
        }
        for option in split_top_level(options_text):
            key = option.split("=", 1)[0].strip()
            if key == "fragile":
                fragile = True
            elif option in alignment:
                mapped_options.append(alignment[option])
            elif key in {"action-spec", "tag-slides", "vertical-alignment"}:
                if key == "action-spec" and "=" in option:
                    raw_value = option.split("=", 1)[1].strip()
                    mapped_value, mode_changed = _map_overlay_mode_spec(
                        raw_value,
                        target_mode,
                    )
                    mapped_options.append("action-spec=" + mapped_value)
                    if mode_changed:
                        findings.append(
                            Finding(
                                "beamer-overlay-mode-mapped",
                                f"Mapped Beamer action-spec mode {raw_value!r} to {mapped_value!r}.",
                                "info",
                                str(relative),
                                line_number(text, opening.start()),
                            )
                        )
                else:
                    mapped_options.append(option)
            elif key == "label" and "=" in option and option.split("=", 1)[1].strip():
                frame_target = option.split("=", 1)[1].strip()
                findings.append(
                    Finding(
                        "beamer-frame-label-mapped",
                        f"Mapped Beamer frame label {frame_target!r} to a PDF hyperlink target.",
                        "info",
                        str(relative),
                        line_number(text, opening.start()),
                    )
                )
            elif key in {"plain", "noframenumbering"}:
                findings.append(
                    Finding(
                        "beamer-frame-visual-option",
                        f"Dropped visual-only Beamer frame option {option!r}.",
                        "info",
                        str(relative),
                        line_number(text, opening.start()),
                    )
                )
            else:
                findings.append(
                    Finding(
                        "beamer-frame-option",
                        f"Dropped unsupported Beamer frame option {option!r}; review the migrated frame.",
                        "error" if strict else "warning",
                        str(relative),
                        line_number(text, opening.start()),
                    )
                )

        environment = "frame*" if fragile else "frame"
        replacement = r"\begin{" + environment + "}" + overlay_text
        if mapped_options:
            replacement += "[" + ",".join(mapped_options) + "]"
        if fragile or title_text:
            replacement += "\n"
        if title_text:
            replacement += r"\frametitle{" + title_text + "}"
        if subtitle_text:
            replacement += "\n" + r"\framesubtitle{" + subtitle_text + "}"
        if frame_target:
            replacement += "\n" + r"\hypertarget{" + frame_target + "}{}"
        replacements.append((opening.start(), cursor, replacement))

        if fragile:
            while close_cursor < len(closings) and closings[close_cursor].start() < opening.end():
                close_cursor += 1
            if close_cursor < len(closings):
                close = closings[close_cursor]
                replacements.append((close.start(), close.end(), r"\end{frame*}"))
                close_cursor += 1
            findings.append(
                Finding(
                    "beamer-fragile-frame",
                    "Converted a fragile Beamer frame to ltx-talk's frame* environment.",
                    file=str(relative),
                    line=line_number(text, opening.start()),
                )
            )
    return apply_replacements(text, replacements)


def _expand_frame_commands(
    text: str,
    relative: Path,
    findings: list[Finding],
) -> str:
    """Convert Beamer's command-form frames to environments for ltx-talk."""

    masked = mask_tex(text)
    replacements: list[tuple[int, int, str]] = []
    frame_ranges: list[tuple[int, int]] = []
    openings = list(re.finditer(r"\\begin\s*\{frame\}", masked))
    closings = list(re.finditer(r"\\end\s*\{frame\}", masked))
    close_index = 0
    for opening in openings:
        while close_index < len(closings) and closings[close_index].start() < opening.end():
            close_index += 1
        if close_index < len(closings):
            frame_ranges.append((opening.start(), closings[close_index].end()))
            close_index += 1
    for match in re.finditer(r"\\frame(?![A-Za-z@])", masked):
        if any(start < match.start() < end for start, end in frame_ranges):
            continue
        cursor = _skip_space(masked, match.end())
        overlay = ""
        if cursor < len(masked) and masked[cursor] == "<":
            end = find_matching(masked, cursor, "<", ">")
            if end:
                overlay = text[cursor:end]
                cursor = _skip_space(masked, end)
        square_groups: list[str] = []
        for _ in range(2):
            if cursor >= len(masked) or masked[cursor] != "[":
                break
            end = find_matching(masked, cursor, "[", "]")
            if end is None:
                break
            square_groups.append(text[cursor:end])
            cursor = _skip_space(masked, end)
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        end = find_matching(masked, cursor, "{", "}")
        if end is None:
            continue
        body = text[cursor + 1 : end - 1]
        replacement = (
            r"\begin{frame}"
            + overlay
            + "".join(square_groups)
            + body
            + r"\end{frame}"
        )
        replacements.append((match.start(), end, replacement))
        findings.append(
            Finding(
                "beamer-frame-command",
                "Converted a command-form Beamer frame to a frame environment.",
                "info",
                str(relative),
                line_number(text, match.start()),
            )
        )
    return apply_replacements(text, replacements)


def _expand_column_commands(
    text: str,
    relative: Path,
    findings: list[Finding],
) -> str:
    """Convert Beamer's command-form columns to ltx-talk environments."""

    # Work from the innermost ``columns`` environment outwards.  Recomputing
    # offsets after each environment avoids overlapping replacements when
    # command-form columns are nested.
    while True:
        masked = mask_tex(text)
        token = re.compile(r"\\(begin|end)\s*\{columns\}")
        stack: list[re.Match[str]] = []
        pairs: list[tuple[re.Match[str], re.Match[str]]] = []
        for match in token.finditer(masked):
            if match.group(1) == "begin":
                stack.append(match)
            elif stack:
                pairs.append((stack.pop(), match))

        converted = False
        # Stack pairing records inner environments first; the explicit sort
        # also makes that invariant clear if the scanner changes later.
        for opening, closing in sorted(
            pairs,
            key=lambda pair: pair[1].start() - pair[0].end(),
        ):
            region_start, region_end = opening.end(), closing.start()
            absolute_commands: list[tuple[int, int]] = []
            for command in re.finditer(
                r"\\column(?![A-Za-z@])",
                masked[region_start:region_end],
            ):
                start = region_start + command.start()
                cursor = _skip_space(masked, region_start + command.end())
                if cursor < len(masked) and masked[cursor] == "<":
                    end = find_matching(masked, cursor, "<", ">")
                    if end is None:
                        continue
                    cursor = _skip_space(masked, end)
                if cursor < len(masked) and masked[cursor] == "[":
                    end = find_matching(masked, cursor, "[", "]")
                    if end is None:
                        continue
                    cursor = _skip_space(masked, end)
                if cursor >= len(masked) or masked[cursor] != "{":
                    continue
                end = find_matching(masked, cursor, "{", "}")
                if end is None:
                    continue
                absolute_commands.append((start, end))

            if not absolute_commands:
                continue
            replacements: list[tuple[int, int, str]] = []
            for index, (start, end) in enumerate(absolute_commands):
                prefix = (
                    r"\begin{column}"
                    if index == 0
                    else "\\end{column}\n\\begin{column}"
                )
                command_end = start + len(r"\column")
                replacements.append((start, end, prefix + text[command_end:end]))
                findings.append(
                    Finding(
                        "beamer-column-command",
                        "Converted a command-form Beamer column to an ltx-talk column environment.",
                        "info",
                        str(relative),
                        line_number(text, start),
                    )
                )
            replacements.append((closing.start(), closing.start(), "\\end{column}\n"))
            text = apply_replacements(text, replacements)
            converted = True
            break
        if not converted:
            return text


def _migrate_columns(
    text: str,
    relative: Path,
    findings: list[Finding],
    *,
    strict: bool,
) -> str:
    """Translate Beamer columns/column option names accepted by ltx-talk."""

    masked = mask_tex(text)
    replacements: list[tuple[int, int, str]] = []
    for environment in ("columns", "column"):
        pattern = re.compile(r"\\begin\s*\{" + environment + r"\}")
        for opening in pattern.finditer(masked):
            cursor = _skip_space(masked, opening.end())
            overlay = ""
            if cursor < len(masked) and masked[cursor] == "<":
                end = find_matching(masked, cursor, "<", ">")
                if end:
                    overlay = text[cursor:end]
                    cursor = _skip_space(masked, end)
            options = ""
            if cursor < len(masked) and masked[cursor] == "[":
                end = find_matching(masked, cursor, "[", "]")
                if end:
                    options = text[cursor + 1 : end - 1]
                    cursor = end

            mapped: list[str] = []
            for option in split_top_level(options):
                key, _, value = option.partition("=")
                key = key.strip()
                if environment == "columns" and key == "onlytextwidth":
                    mapped.append(r"width=\textwidth")
                elif environment == "columns" and key == "totalwidth" and value.strip():
                    mapped.append("width=" + value.strip())
                elif environment == "columns" and key == "width" and value.strip():
                    mapped.append(option)
                elif environment == "column" and key in {"t", "b", "c"}:
                    mapped.append(key)
                elif environment == "column" and key == "T":
                    mapped.append("t")
                elif environment == "columns" and key in {"T", "t", "b", "c"}:
                    findings.append(
                        Finding(
                            "beamer-columns-alignment",
                            f"Dropped Beamer columns alignment option {option!r}; individual column alignment is preserved where possible.",
                            "info",
                            str(relative),
                            line_number(text, opening.start()),
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "beamer-columns-option",
                            f"Dropped unsupported {environment} option {option!r}; review the migrated layout.",
                            "error" if strict else "warning",
                            str(relative),
                            line_number(text, opening.start()),
                        )
                    )
            replacement = r"\begin{" + environment + "}" + overlay
            if mapped:
                replacement += "[" + ",".join(mapped) + "]"
            replacements.append((opening.start(), cursor, replacement))
    return apply_replacements(text, replacements)


def _find_unsupported_beamer_constructs(
    text: str,
    relative: Path,
) -> list[Finding]:
    """Report content that cannot be safely approximated in a static tagged copy."""

    masked = mask_tex(text)
    patterns = {
        "againframe": "Replayed frames cannot be migrated automatically; duplicate the frame content explicitly.",
        "movie": "Embedded Beamer movies cannot be represented in the static accessible PDF.",
        "sound": "Embedded Beamer audio cannot be represented in the static accessible PDF.",
        "animate": "Beamer animation commands require a manual static alternative.",
        "framezoom": "Beamer frame zoom regions require a manual static alternative.",
        "sectionpage": "Replace an explicit \\sectionpage transition frame with a normal frame title and meaningful content.",
        "subsectionpage": "Replace an explicit \\subsectionpage transition frame with a normal frame title and meaningful content.",
        "partpage": "Replace an explicit \\partpage transition frame with a normal frame title and meaningful content.",
    }
    findings: list[Finding] = []
    for command, message in patterns.items():
        for match in re.finditer(r"\\" + command + r"(?![A-Za-z@])", masked):
            findings.append(
                Finding(
                    "unsupported-beamer-" + command,
                    message,
                    "error",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
    for match in re.finditer(r"\\beamer@[A-Za-z@]+", masked):
        findings.append(
            Finding(
                "unsupported-beamer-internal",
                f"Beamer internal command {match.group(0)!r} cannot be migrated safely.",
                "error",
                str(relative),
                line_number(text, match.start()),
            )
        )
    return findings


def _drop_package_from_usepackage(text: str, package: str) -> tuple[str, bool]:
    """Remove one package from active ``\\usepackage`` declarations."""

    replacements: list[tuple[int, int, str]] = []
    removed = False
    for command in iter_commands(text, "usepackage"):
        packages = split_top_level(group_content(text, command.mandatory) or "")
        kept = [item for item in packages if item.strip() != package]
        if len(kept) == len(packages):
            continue
        removed = True
        if kept:
            optional = (
                text[command.optional[0] : command.optional[1]]
                if command.optional is not None
                else ""
            )
            replacement = r"\usepackage" + optional + "{" + ",".join(kept) + "}"
        else:
            replacement = _blank_preserving_lines(text, command.start, command.end)
        replacements.append((command.start, command.end, replacement))
    return apply_replacements(text, replacements), removed


def _migrate_subfigure_environments(
    text: str,
    relative: Path,
    findings: list[Finding],
) -> str:
    """Replace subcaption's Beamer subfigures with tagged-safe minipages."""

    text, removed_package = _drop_package_from_usepackage(text, "subcaption")
    masked = mask_tex(text)
    token = re.compile(r"\\(begin|end)\s*\{subfigure\}")
    stack: list[re.Match[str]] = []
    pairs: list[tuple[re.Match[str], re.Match[str]]] = []
    for match in token.finditer(masked):
        if match.group(1) == "begin":
            stack.append(match)
        elif stack:
            pairs.append((stack.pop(), match))

    replacements: list[tuple[int, int, str]] = []
    captions = iter_commands(text, "caption")
    for opening, closing in pairs:
        cursor = _skip_space(masked, opening.end())
        alignment = ""
        if cursor < len(masked) and masked[cursor] == "[":
            alignment_end = find_matching(masked, cursor, "[", "]")
            if alignment_end is None:
                continue
            alignment = text[cursor:alignment_end]
            cursor = _skip_space(masked, alignment_end)
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        width_end = find_matching(masked, cursor, "{", "}")
        if width_end is None:
            continue
        width = text[cursor:width_end]
        replacements.append(
            (
                opening.start(),
                width_end,
                r"\begin{minipage}" + alignment + width,
            )
        )
        replacements.append((closing.start(), closing.end(), r"\end{minipage}"))
        for caption in captions:
            if not (width_end <= caption.start < closing.start()):
                continue
            caption_text = group_content(text, caption.mandatory) or ""
            plain_caption = latex_to_plain(caption_text)
            plain_caption = (
                plain_caption.replace("$", "")
                .replace(r"\(", "")
                .replace(r"\)", "")
                .strip()
            )
            replacements.append(
                (
                    caption.start,
                    caption.end,
                    r"\par\small\centering "
                    + escape_tex_text(plain_caption)
                    + r"\par",
                )
            )
        findings.append(
            Finding(
                "beamer-subfigure-migrated",
                "Converted a subcaption subfigure to a minipage while retaining its visible panel caption.",
                "info",
                str(relative),
                line_number(text, opening.start()),
            )
        )

    if removed_package:
        findings.append(
            Finding(
                "beamer-subcaption-removed",
                "Removed the incompatible subcaption package after converting its subfigure environments.",
                "info",
                str(relative),
            )
        )
    return apply_replacements(text, replacements)


def _migrate_float_placement_options(
    text: str,
    relative: Path,
    findings: list[Finding],
) -> str:
    """Map traditional figure/table placement options to ltx-talk keys."""

    masked = mask_tex(text)
    replacements: list[tuple[int, int, str]] = []
    for opening in re.finditer(r"\\begin\s*\{(figure|table)\}", masked):
        cursor = _skip_space(masked, opening.end())
        if cursor < len(masked) and masked[cursor] == "<":
            overlay_end = find_matching(masked, cursor, "<", ">")
            if overlay_end is None:
                continue
            cursor = _skip_space(masked, overlay_end)
        if cursor >= len(masked) or masked[cursor] != "[":
            continue
        option_end = find_matching(masked, cursor, "[", "]")
        if option_end is None:
            continue
        options = text[cursor + 1 : option_end - 1].strip()
        if not re.fullmatch(r"[!Hhtbp]+", options):
            continue
        replacements.append(
            (cursor, option_end, "[float-placement={" + options + "}]")
        )
        findings.append(
            Finding(
                "beamer-float-placement-mapped",
                f"Mapped {opening.group(1)} placement {options!r} to ltx-talk's float-placement key.",
                "info",
                str(relative),
                line_number(text, opening.start()),
            )
        )
    return apply_replacements(text, replacements)


def migrate_beamer_content(
    text: str,
    relative: Path,
    config: ToolConfig,
) -> tuple[str, list[Finding]]:
    """Migrate frame syntax in a main or included file from Beamer."""

    findings: list[Finding] = []
    text, preprocessing_findings = _preprocess_beamer_content(
        text,
        relative,
        config.beamer_target_mode,
        strict=config.strict,
    )
    findings.extend(preprocessing_findings)
    findings.extend(_find_unsupported_beamer_constructs(text, relative))
    text = _expand_frame_commands(text, relative, findings)
    text, vspace_findings = _normalize_standalone_vspace_linebreaks(text, relative)
    findings.extend(vspace_findings)
    text = _migrate_frames(
        text,
        relative,
        findings,
        strict=config.strict,
        target_mode=config.beamer_target_mode,
    )
    text = _expand_column_commands(text, relative, findings)
    text = _migrate_columns(text, relative, findings, strict=config.strict)
    text = _migrate_subfigure_environments(text, relative, findings)
    text = _migrate_float_placement_options(text, relative, findings)
    masked = mask_tex(text)
    titlepage_replacements = [
        (match.start(), match.end(), r"\maketitle")
        for match in re.finditer(r"\\titlepage(?![A-Za-z@])", masked)
    ]
    text = apply_replacements(text, titlepage_replacements)
    return text, findings


def instrument_main(
    text: str,
    relative: Path,
    config: ToolConfig,
) -> tuple[str, str, list[Finding], str]:
    """Add PDF metadata/tagging and migrate Beamer to ltx-talk when requested."""

    findings: list[Finding] = []
    text = _remove_generated_blocks(text)
    document_class, class_match = detect_document_class(text)
    detected_profile = "beamer" if document_class == "beamer" else document_class
    profile_matches = (
        config.profile == "auto"
        or config.profile == detected_profile
        or (config.profile == "article" and detected_profile not in {"beamer", "ltx-talk"})
        or (config.profile == "ltx-talk" and detected_profile in {"beamer", "ltx-talk"})
    )
    if not profile_matches:
        raise ValueError(
            f"Requested profile {config.profile!r} does not match document class {document_class!r}"
        )

    migrated = False
    if document_class == "beamer":
        if config.beamer_mode == "reject":
            raise UnsupportedDocumentError(
                "Beamer cannot produce tagged PDF. Re-run with --beamer-mode ltx-talk "
                "to migrate the copied project."
            )
        class_options = group_content(text, class_match.optional)
        mapped_options, dropped_options, xcolor_options = _class_options_for_ltx_talk(
            class_options
        )
        replacement = r"\documentclass" + mapped_options + "{ltx-talk}"
        if xcolor_options:
            replacement = (
                r"\PassOptionsToPackage{"
                + ",".join(xcolor_options)
                + r"}{xcolor}"
                + "\n"
                + replacement
            )
        text = text[: class_match.start] + replacement + text[class_match.end :]
        text, frame_findings = migrate_beamer_content(text, relative, config)
        findings.extend(frame_findings)
        for option in dropped_options:
            findings.append(
                Finding(
                    "beamer-class-option",
                    f"Dropped unsupported Beamer class option {option!r}; review the migrated layout.",
                    "error" if config.strict else "warning",
                    str(relative),
                    line_number(text, class_match.start),
                )
            )
        migrated = True
        findings.append(
            Finding(
                "beamer-migrated",
                "Replaced unsupported Beamer with ltx-talk in the copied project; review overlays and visual-only theme customizations.",
                "warning",
                str(relative),
                line_number(text, class_match.start),
            )
        )

    # Work from fresh offsets after class migration.
    metadata_matches = iter_commands(text, "DocumentMetadata")
    existing = None
    if metadata_matches:
        existing = group_content(text, metadata_matches[0].mandatory)
        metadata_replacements: list[tuple[int, int, str]] = []
        for item in metadata_matches:
            start = text.rfind("\n", 0, item.start) + 1
            next_newline = text.find("\n", item.end)
            end = len(text) if next_newline < 0 else next_newline + 1
            if text[start:item.start].strip() or text[item.end:end].strip():
                start, end = item.start, item.end
            metadata_replacements.append((start, end, ""))
        text = apply_replacements(text, metadata_replacements)

    current_class, class_match = detect_document_class(text)
    metadata = "\\DocumentMetadata{\n  " + _metadata_content(existing, config.language) + "\n}\n"
    text = text[: class_match.start] + metadata + text[class_match.start :]

    # ltx-talk compatibility commands must exist before the source preamble's
    # Beamer theme/template declarations are encountered.
    if current_class == "ltx-talk":
        _, class_match = detect_document_class(text)
        needs_amsthm = bool(
            re.search(
                r"\\(?:theoremstyle|newtheorem|newtheoremstyle)(?![A-Za-z@])",
                mask_tex(text),
            )
        )
        local_color_theme_setup = "".join(
            rf"\RequirePackage{{{package}}}" + "\n"
            for package in config.local_beamer_color_themes
        )
        early_setup = (
            "\n"
            + BEGIN_MARKER
            + "\n"
            + r"\input{canvasally-ltx-talk-shim.tex}"
            + "\n"
            + (r"\RequirePackage{amsthm}" + "\n" if needs_amsthm else "")
            + local_color_theme_setup
            + END_MARKER
            + "\n"
        )
        text = text[: class_match.end] + early_setup + text[class_match.end :]
        for package in config.local_beamer_color_themes:
            findings.append(
                Finding(
                    "beamer-local-color-theme",
                    f"Loaded local color theme package {package!r} to preserve source colors and generic package setup.",
                    "info",
                    str(relative),
                    line_number(text, class_match.start),
                )
            )
        if needs_amsthm:
            findings.append(
                Finding(
                    "beamer-amsthm-restored",
                    "Loaded amsthm because the Beamer source uses theorem declarations that ltx-talk does not load implicitly.",
                    "info",
                    str(relative),
                    line_number(text, class_match.start),
                )
            )

    # Extract the source title unless a plain-text override was supplied.
    title = config.title
    if title is None:
        title_matches = iter_commands(text, "title")
        if title_matches:
            title = latex_to_plain(group_content(text, title_matches[0].mandatory) or "")
    if not title:
        title = relative.stem.replace("_", " ").replace("-", " ").strip().title()
        findings.append(
            Finding(
                "generated-title",
                f"No document title was found; generated PDF title {title!r}.",
                "warning",
                str(relative),
            )
        )

    begin_matches = iter_commands(text, "begin")
    document_begin = next(
        (item for item in begin_matches if group_content(text, item.mandatory) == "document"),
        None,
    )
    if document_begin is None:
        raise ValueError("No active \\begin{document} found in the main file")

    setup_lines = [
        BEGIN_MARKER,
        r"\usepackage{canvasally}",
        r"\renewcommand\CanvasAllyTitle{" + escape_tex_text(title) + "}",
        r"\renewcommand\CanvasAllyLanguage{" + escape_tex_text(config.language) + "}",
    ]
    if current_class == "ltx-talk":
        setup_lines.append(r"\input{canvasally-ltx-talk-content-shim.tex}")
    setup_lines.append(r"\CanvasAllyApplyMetadata")
    setup_lines.extend([END_MARKER, ""])
    setup = "\n".join(setup_lines)
    text = text[: document_begin.start] + setup + text[document_begin.start :]
    profile = "ltx-talk" if migrated or document_class == "ltx-talk" else document_class
    return text, profile, findings, title


def _strip_outer_braces(value: str) -> str:
    value = value.strip()
    while value.startswith("{"):
        end = find_matching(value, 0, "{", "}")
        if end != len(value):
            break
        value = value[1:-1].strip()
    return value


def _has_irreversible_tag_disable(
    options: str,
    kind: str | None,
    *,
    nested: bool = False,
) -> bool:
    for item in split_top_level(options):
        key, separator, raw_value = item.partition("=")
        key = key.strip().lower()
        value = _strip_outer_braces(raw_value).strip() if separator else ""
        if key == "tagging-setup" and separator:
            if value.lower() == "false":
                return True
            if _has_irreversible_tag_disable(value, "picture", nested=True):
                return True
        elif nested and key == "false" and not separator:
            return True
        elif key == "tag" and value.lower() == "false" and kind in {
            "graphic",
            "picture",
        }:
            return True
    return False


def _annotation_mode(
    options: str,
    *,
    nested: bool = False,
    kind: str | None = None,
) -> tuple[str | None, str | None]:
    """Return the effective native graphic mode and text.

    Keys are processed in source order because later graphic keys override
    earlier ones.  ``text`` and ``off`` are tagging modes only inside the
    documented ``tagging-setup`` key; at TikZ's top level, for example,
    ``text=blue`` is an ordinary styling key.
    """

    if _has_irreversible_tag_disable(options, kind):
        return "off", None

    mode: str | None = None
    mode_value: str | None = None
    for item in split_top_level(options):
        key, separator, raw_value = item.partition("=")
        key = key.strip().lower()
        value = _strip_outer_braces(raw_value) if separator else ""
        if key == "alt":
            mode, mode_value = "alt", value
            continue
        if key == "artifact":
            mode, mode_value = "artifact", None
            continue
        if key == "actualtext":
            mode, mode_value = "actualtext", value
            continue
        if key == "tagging-setup" and separator:
            if value.lower() == "false":
                mode, mode_value = "off", None
                continue
            nested_mode, nested_value = _annotation_mode(
                value,
                nested=True,
                kind=kind,
            )
            if nested_mode is not None:
                mode, mode_value = nested_mode, nested_value
            continue
        generic_direct = nested or kind == "picture"
        legacy_tag_direct = nested or kind in {"graphic", "picture"}
        if not generic_direct and not legacy_tag_direct:
            continue
        if generic_direct and key in {"text", "off"}:
            mode, mode_value = key, None
        elif nested and key == "false" and not separator:
            mode, mode_value = "off", None
        elif generic_direct and key == "mode" and value.lower() in {
            "alt",
            "artifact",
            "actualtext",
            "text",
            "off",
        }:
            mode, mode_value = value.lower(), mode_value
        elif legacy_tag_direct and key == "tag" and separator:
            if value.lower() == "artifact":
                mode, mode_value = "artifact", None
            elif value.lower() == "false":
                mode, mode_value = "off", None
            # Any other value selects a structure type (for example Formula),
            # not an alternative-text mode.  Leave the current mode unchanged
            # so an unannotated custom-tagged visual can receive a sibling alt.
    return mode, mode_value


def _annotation_structure_tag(options: str, kind: str | None = None) -> str | None:
    """Return a custom structure tag selected inside ``tagging-setup``."""

    structure_tag: str | None = None
    for item in split_top_level(options):
        key, separator, raw_value = item.partition("=")
        key = key.strip().lower()
        if key == "tagging-setup" and separator:
            nested = _strip_outer_braces(raw_value)
            nested_tag = _annotation_structure_tag(nested, kind="picture")
            if nested_tag is not None:
                structure_tag = nested_tag
        elif key == "tag" and separator and kind in {"graphic", "picture"}:
            value = _strip_outer_braces(raw_value).strip()
            structure_tag = value or None
    return structure_tag


def _replace_direct_annotation(
    options: str,
    current_mode: str,
    alt: str | None,
) -> str | None:
    """Replace the effective top-level native mode, or None if it is nested."""

    items = split_top_level(options)
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        key = item.partition("=")[0].strip().lower()
        if key != current_mode:
            continue
        items[index] = (
            "artifact"
            if alt is None
            else "alt={" + escape_tex_text(_normalize_alt_text(alt)) + "}"
        )
        return ",".join(items)
    return None


def _placeholder_label(value: str | None, kind: str) -> str | None:
    if value is None:
        return None
    del kind  # Every reserved placeholder prefix requires review on every visual kind.
    match = re.fullmatch(
        r"(?:tikz|graphic|picture|visual)[1-9][0-9]*",
        value.strip(),
        re.I,
    )
    return match.group(0).lower() if match else None


def _lookup_alt(config: ToolConfig, relative: Path, asset: str) -> tuple[bool, str | None]:
    exact = f"{relative.as_posix()}::{asset}"
    for key in (exact, asset, Path(asset).as_posix(), Path(asset).name):
        if key in config.image_alts:
            return True, config.image_alts[key]
    return False, None


def _contains_native_default(value: str) -> bool:
    return bool(
        re.search(
            r"(?:^|[,{}\s])(?:alt|actualtext|artifact|tagging-setup|tag)\s*(?:=|,|}|$)",
            value,
            re.I,
        )
    )


def _has_stateful_native_default(text: str, kind: str) -> bool:
    """Conservatively detect file-level graphic/TikZ tagging defaults."""

    if kind == "tikz":
        return any(
            _contains_native_default(group_content(text, command.mandatory) or "")
            for command in iter_commands(text, "tikzset")
            if command.mandatory is not None
        )
    if kind != "graphic":
        return False

    masked = mask_tex(text)
    for match in re.finditer(r"\\setkeys(?![A-Za-z@])", masked):
        cursor = _skip_space(masked, match.end())
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        family_end = find_matching(masked, cursor, "{", "}")
        if family_end is None:
            continue
        family = text[cursor + 1 : family_end - 1].strip()
        cursor = _skip_space(masked, family_end)
        if family.lower() != "gin" or cursor >= len(masked) or masked[cursor] != "{":
            continue
        value_end = find_matching(masked, cursor, "{", "}")
        if value_end is not None and _contains_native_default(
            text[cursor + 1 : value_end - 1]
        ):
            return True
    return False


def _preceding_alt_directive_record(text: str, offset: int) -> AltDirective | None:
    line_start = text.rfind("\n", 0, offset) + 1
    # A directive belongs only to the first non-whitespace construct on the
    # following line.  Otherwise a marker for an earlier same-line visual can
    # leak into a later graphic, TikZ command, or picture environment.
    if text[line_start:offset].strip():
        return None
    previous_end = line_start - 1
    previous_start = text.rfind("\n", 0, max(0, previous_end)) + 1
    previous_raw = text[previous_start:previous_end]
    previous = previous_raw.strip()
    match = re.fullmatch(
        r"%\s*allytex:\s*(?:alt\s*=\s*(.+)|decorative)\s*",
        previous,
        re.I,
    )
    if not match:
        return None
    indent_match = re.match(r"[ \t]*", previous_raw)
    return AltDirective(
        match.group(1).strip() if match.group(1) else None,
        previous_start,
        previous_end,
        indent_match.group(0) if indent_match else "",
    )


def _directive_source(alt: str | None) -> str:
    if alt is None:
        return "% allytex: decorative"
    return "% allytex: alt=" + _normalize_alt_text(alt)


def _normalize_alt_text(alt: str) -> str:
    return re.sub(r"\s+", " ", alt.strip())


def _directive_insertion(text: str, offset: int, alt: str | None) -> tuple[int, int, str]:
    """Create a preceding directive insertion without commenting live TeX."""

    directive = _directive_source(alt)
    line_start = text.rfind("\n", 0, offset) + 1
    prefix = text[line_start:offset]
    if prefix.strip():
        return offset, offset, "\n" + directive + "\n"
    return line_start, line_start, prefix + directive + "\n"


def _directive_replacement(directive: AltDirective, alt: str | None) -> tuple[int, int, str]:
    return directive.start, directive.end, directive.indent + _directive_source(alt)


def _placeholder_finding(
    *,
    code: str,
    label: str,
    object_name: str,
    relative: Path,
    line: int,
    strict: bool,
    inserted: bool,
) -> Finding:
    action = "Inserted" if inserted else "Retained"
    return Finding(
        code,
        f"{action} placeholder '% allytex: alt={label}' for {object_name}; replace it with a meaningful description.",
        "error" if strict else "warning",
        str(relative),
        line,
    )


def _caption_for_graphic(text: str, offset: int) -> str | None:
    masked = mask_tex(text)
    figure_ranges = [
        bounds
        for bounds in _environment_ranges(masked, "figure")
        if bounds[0] <= offset < bounds[1]
    ]
    if not figure_ranges:
        return None
    figure_start, figure_end = min(
        figure_ranges,
        key=lambda bounds: bounds[1] - bounds[0],
    )
    subfigure_ranges = [
        bounds
        for bounds in _environment_ranges(masked, "subfigure")
        if figure_start <= bounds[0] and bounds[1] <= figure_end
    ]
    containing_subfigure = next(
        (
            bounds
            for bounds in sorted(
                subfigure_ranges,
                key=lambda item: item[1] - item[0],
            )
            if bounds[0] <= offset < bounds[1]
        ),
        None,
    )

    panel_caption: str | None = None
    parent_caption: str | None = None
    for caption in iter_commands(text, "caption"):
        if not (figure_start <= caption.start < figure_end):
            continue
        value = latex_to_plain(group_content(text, caption.mandatory) or "") or None
        if value is None:
            continue
        if containing_subfigure is not None and (
            containing_subfigure[0] <= caption.start < containing_subfigure[1]
        ):
            panel_caption = panel_caption or value
            continue
        if any(start <= caption.start < end for start, end in subfigure_ranges):
            continue
        parent_caption = parent_caption or value

    if parent_caption and panel_caption:
        return parent_caption.rstrip(". ") + "; panel " + panel_caption
    return panel_caption or parent_caption


def _adjacent_drawing_description(text: str, offset: int) -> str | None:
    """Read a short explicitly styled description immediately after a drawing."""

    masked = mask_tex(text)
    cursor = _skip_space(masked, offset)
    command = re.match(
        r"\\(?:small|footnotesize|scriptsize|tiny|normalsize)(?![A-Za-z@])",
        masked[cursor:],
    )
    if command is None:
        return None
    cursor = _skip_space(masked, cursor + command.end())
    if cursor >= len(masked) or masked[cursor] != "{":
        return None
    end = find_matching(masked, cursor, "{", "}")
    if end is None:
        return None
    raw = text[cursor + 1 : end - 1]
    if r"\begin" in mask_tex(raw) or any(
        marker in mask_tex(raw)
        for marker in (r"\includegraphics", r"\tikz", r"\begin{picture}")
    ):
        return None
    value = latex_to_plain(raw)
    if len(value) < 8 or len(value) > 500:
        return None
    return value


def _handle_existing_annotation(
    *,
    text: str,
    offset: int,
    option_group: tuple[int, int] | None,
    options: str,
    kind: str,
    object_name: str,
    code: str,
    relative: Path,
    config: ToolConfig,
    placeholder_allocator: PlaceholderAllocator,
    replacements: list[tuple[int, int, str]],
    findings: list[Finding],
) -> bool:
    """Preserve native annotations and keep generated placeholders reviewable."""

    mode, value = _annotation_mode(options, kind=kind)
    if mode is None:
        return False
    directive = _preceding_alt_directive_record(text, offset)
    line = line_number(text, offset)

    if mode == "alt":
        current = value or ""
        if not current.strip() and directive is None:
            placeholder = placeholder_allocator.allocate(kind)
            replacement_options = _replace_direct_annotation(
                options,
                "alt",
                placeholder,
            )
            if replacement_options is None or option_group is None:
                findings.append(
                    Finding(
                        code + "-empty-alt",
                        f"{object_name.capitalize()} has an empty nested alternative that cannot be replaced safely.",
                        "error",
                        str(relative),
                        line,
                    )
                )
                return True
            replacements.append(
                (option_group[0], option_group[1], "[" + replacement_options + "]")
            )
            replacements.append(_directive_insertion(text, offset, placeholder))
            findings.append(
                _placeholder_finding(
                    code=code + "-placeholder-alt",
                    label=placeholder,
                    object_name=object_name,
                    relative=relative,
                    line=line,
                    strict=config.strict,
                    inserted=True,
                )
            )
            return True
        current_placeholder = _placeholder_label(current, kind)
        directive_text = (
            _normalize_alt_text(directive.alt)
            if directive is not None and directive.alt is not None
            else None
        )
        current_text = _normalize_alt_text(current)
        directive_matches = bool(
            directive_text is not None
            and (
                directive_text == current_text
                or escape_tex_text(directive_text) == current_text
            )
        )
        if directive is not None and not directive_matches:
            directive_placeholder = (
                _placeholder_label(directive.alt, kind)
                if directive.alt is not None
                else None
            )
            # The meaningful side wins when only one side is still a generated
            # placeholder.  This lets authors edit either the durable comment
            # or the native option without a later run undoing their work.
            if current.strip() and not current_placeholder and directive_placeholder:
                replacements.append(_directive_replacement(directive, current.strip()))
                findings.append(
                    Finding(
                        code + "-directive-synced",
                        f"Updated the stale placeholder directive from the authored native alternative on {object_name}.",
                        "info",
                        str(relative),
                        line,
                    )
                )
                return True

            if current_placeholder or not current.strip():
                replacement_options = _replace_direct_annotation(
                    options,
                    "alt",
                    directive.alt,
                )
                if replacement_options is not None and option_group is not None:
                    replacements.append(
                        (option_group[0], option_group[1], "[" + replacement_options + "]")
                    )
                    if directive.alt is None:
                        findings.append(
                            Finding(
                                code + "-directive-synced",
                                f"Updated {object_name} from a generated alternative to decorative, following its allytex directive.",
                                "info",
                                str(relative),
                                line,
                            )
                        )
                        return True
                    if directive_placeholder:
                        findings.append(
                            _placeholder_finding(
                                code=code + "-placeholder-alt",
                                label=directive_placeholder,
                                object_name=object_name,
                                relative=relative,
                                line=line,
                                strict=config.strict,
                                inserted=False,
                            )
                        )
                    else:
                        findings.append(
                            Finding(
                                code + "-directive-synced",
                                f"Updated {object_name} alternative text from its edited allytex directive.",
                                "info",
                                str(relative),
                                line,
                            )
                        )
                    return True
            findings.append(
                Finding(
                    code + "-annotation-conflict",
                    f"{object_name.capitalize()} has two differing meaningful annotations in its native alt option and allytex directive; reconcile them manually.",
                    "error",
                    str(relative),
                    line,
                )
            )
            return True

        if current_placeholder:
            inserted_directive = directive is None
            if inserted_directive:
                replacements.append(_directive_insertion(text, offset, current_placeholder))
            findings.append(
                _placeholder_finding(
                    code=code + "-placeholder-alt",
                    label=current_placeholder,
                    object_name=object_name,
                    relative=relative,
                    line=line,
                    strict=config.strict,
                    inserted=inserted_directive,
                )
            )
        return True

    if mode == "actualtext" and not (value or "").strip():
        replacement_alt = directive.alt if directive is not None else None
        inserted = directive is None
        if inserted:
            replacement_alt = placeholder_allocator.allocate(kind)
        replacement_options = _replace_direct_annotation(
            options,
            "actualtext",
            replacement_alt,
        )
        if replacement_options is None or option_group is None:
            findings.append(
                Finding(
                    code + "-empty-actualtext",
                    f"{object_name.capitalize()} has empty nested actualtext and would disappear from the accessible reading order.",
                    "error",
                    str(relative),
                    line,
                )
            )
            return True
        replacements.append(
            (option_group[0], option_group[1], "[" + replacement_options + "]")
        )
        if inserted:
            replacements.append(_directive_insertion(text, offset, replacement_alt))
            findings.append(
                _placeholder_finding(
                    code=code + "-placeholder-alt",
                    label=replacement_alt or kind,
                    object_name=object_name,
                    relative=relative,
                    line=line,
                    strict=config.strict,
                    inserted=True,
                )
            )
        else:
            findings.append(
                Finding(
                    code + "-directive-synced",
                    f"Replaced empty actualtext on {object_name} from its allytex directive.",
                    "info",
                    str(relative),
                    line,
                )
            )
        return True

    if mode == "actualtext":
        actual_placeholder = _placeholder_label(value, kind)
        if actual_placeholder:
            replacement_alt = (
                directive.alt if directive is not None else actual_placeholder
            )
            replacement_options = _replace_direct_annotation(
                options,
                "actualtext",
                replacement_alt,
            )
            if replacement_options is None or option_group is None:
                findings.append(
                    Finding(
                        code + "-placeholder-actualtext",
                        f"{object_name.capitalize()} uses placeholder actualtext in a nested mode that cannot be replaced safely.",
                        "error",
                        str(relative),
                        line,
                    )
                )
                return True
            replacements.append(
                (option_group[0], option_group[1], "[" + replacement_options + "]")
            )
            if directive is None:
                replacements.append(
                    _directive_insertion(text, offset, replacement_alt)
                )
            if replacement_alt is not None and _placeholder_label(replacement_alt, kind):
                findings.append(
                    _placeholder_finding(
                        code=code + "-placeholder-alt",
                        label=replacement_alt,
                        object_name=object_name,
                        relative=relative,
                        line=line,
                        strict=config.strict,
                        inserted=directive is None,
                    )
                )
            else:
                findings.append(
                    Finding(
                        code + "-directive-synced",
                        f"Replaced placeholder actualtext on {object_name} from its edited allytex directive.",
                        "info",
                        str(relative),
                        line,
                    )
                )
            return True
        if (
            directive is not None
            and directive.alt is not None
            and _placeholder_label(directive.alt, kind)
        ):
            replacements.append(_directive_replacement(directive, value))
            findings.append(
                Finding(
                    code + "-directive-synced",
                    f"Updated the stale placeholder directive from meaningful actualtext on {object_name}.",
                    "info",
                    str(relative),
                    line,
                )
            )
            return True

    if mode == "off":
        findings.append(
            Finding(
                code + "-tagging-disabled",
                f"Tagging is disabled for visible {object_name}; use meaningful alt text, actualtext for a symbol, or artifact for a decorative visual.",
                "error",
                str(relative),
                line,
            )
        )
        return True

    if mode == "artifact" and directive is not None and directive.alt is not None:
        if _placeholder_label(directive.alt, kind):
            replacements.append(_directive_replacement(directive, None))
            findings.append(
                Finding(
                    code + "-directive-synced",
                    f"Updated the stale placeholder directive for decorative {object_name}.",
                    "info",
                    str(relative),
                    line,
                )
            )
        else:
            replacement_options = _replace_direct_annotation(
                options,
                "artifact",
                directive.alt,
            )
            if replacement_options is not None and option_group is not None:
                replacements.append(
                    (option_group[0], option_group[1], "[" + replacement_options + "]")
                )
                findings.append(
                    Finding(
                        code + "-directive-synced",
                        f"Changed {object_name} from decorative to informative using its edited allytex directive.",
                        "info",
                        str(relative),
                        line,
                    )
                )
            else:
                findings.append(
                    Finding(
                        code + "-annotation-conflict",
                        f"{object_name.capitalize()} has nested artifact tagging but a meaningful allytex directive; reconcile them manually.",
                        "error",
                        str(relative),
                        line,
                    )
                )
    # actualtext, text, and artifact are deliberate non-Figure modes.
    return True


def transform_graphics(
    text: str,
    relative: Path,
    config: ToolConfig,
    placeholder_allocator: PlaceholderAllocator | None = None,
) -> tuple[str, list[Finding]]:
    """Add explicit alt/artifact options to active includegraphics commands."""

    # Materialize configured mappings first so a meaningful map can replace a
    # generated placeholder even when this public transform is used directly.
    text = apply_replacements(
        text,
        _graphic_map_replacements(text, relative, config),
    )
    if placeholder_allocator is None:
        placeholder_allocator = PlaceholderAllocator.from_sources(
            [text],
            [value for value in config.image_alts.values() if value is not None],
        )
    findings: list[Finding] = []
    replacements: list[tuple[int, int, str]] = []
    stateful_default = (
        config.stateful_graphic_default
        or _has_stateful_native_default(text, "graphic")
    )
    outer_drawing_ranges = [
        (drawing.start, drawing.end)
        for drawing in _owned_native_drawings(text)
        if _drawing_suppresses_children(text, drawing)
    ]
    for match in iter_graphic_commands(text):
        if any(start <= match.start < end for start, end in outer_drawing_ranges):
            continue
        asset = (group_content(text, match.mandatory) or "").strip()
        if not asset:
            continue
        option_values = [group_content(text, group) or "" for group in match.optionals]
        options = ",".join(option_values)
        first_optional = match.optionals[0] if len(match.optionals) == 1 else None
        if _handle_existing_annotation(
            text=text,
            offset=match.start,
            option_group=first_optional,
            options=options,
            kind="graphic",
            object_name=f"graphic {asset!r}",
            code="graphic",
            relative=relative,
            config=config,
            placeholder_allocator=placeholder_allocator,
            replacements=replacements,
            findings=findings,
        ):
            continue
        if len(match.optionals) == 2:
            findings.append(
                Finding(
                    "graphic-two-optionals",
                    f"Graphic {asset!r} uses legacy two-optional-argument syntax; convert it to one graphicx key-value option so alt text can be added safely.",
                    "error",
                    str(relative),
                    line_number(text, match.start),
                )
            )
            continue

        directive = _preceding_alt_directive_record(text, match.start)
        found, alt = directive is not None, directive.alt if directive else None
        source = "directive"
        if not found:
            found, alt = _lookup_alt(config, relative, asset)
            source = "map"
        if not found and stateful_default:
            findings.append(
                Finding(
                    "graphic-stateful-annotation",
                    f"Graphic {asset!r} may inherit a stateful Gin accessibility setting; add a local allytex directive or native option before placeholder automation.",
                    "error",
                    str(relative),
                    line_number(text, match.start),
                )
            )
            continue
        if not found:
            alt = _caption_for_graphic(text, match.start)
            found = alt is not None
            source = "caption"

        if found and alt is not None and not alt.strip():
            found = False
        placeholder = bool(
            found
            and alt is not None
            and _placeholder_label(alt, "graphic") is not None
        )
        if not found:
            alt = placeholder_allocator.allocate("graphic")
            found = True
            source = "generated placeholder"
            placeholder = True

        if source != "directive":
            replacements.append(_directive_insertion(text, match.start, alt))

        if found and alt is None:
            new_option = "artifact"
            message = f"Tagged decorative graphic {asset!r} as an artifact ({source})."
            severity = "info"
        elif alt is not None:
            new_option = "alt={" + escape_tex_text(_normalize_alt_text(alt)) + "}"
            message = f"Added alternative text to graphic {asset!r} from {source}."
            severity = "info"
        else:  # pragma: no cover - found decorative alternatives are handled above.
            continue

        if match.optionals:
            start, end = match.optionals[0]
            replacement = "[" + new_option + ("," + option_values[0] if option_values[0].strip() else "") + "]"
        else:
            start = end = match.option_insert
            replacement = "[" + new_option + "]"
        replacements.append((start, end, replacement))
        if placeholder:
            findings.append(
                _placeholder_finding(
                    code="graphic-placeholder-alt",
                    label=alt or "graphic",
                    object_name=f"graphic {asset!r}",
                    relative=relative,
                    line=line_number(text, match.start),
                    strict=config.strict,
                    inserted=True,
                )
            )
        else:
            findings.append(
                Finding(
                    "graphic-alt",
                    message,
                    severity,
                    str(relative),
                    line_number(text, match.start),
                )
            )
    return apply_replacements(text, replacements), findings


def _environment_ranges(masked: str, environment: str) -> list[tuple[int, int]]:
    token = re.compile(r"\\(begin|end)\s*\{" + re.escape(environment) + r"\}")
    stack: list[re.Match[str]] = []
    ranges: list[tuple[int, int]] = []
    for match in token.finditer(masked):
        if match.group(1) == "begin":
            stack.append(match)
        elif stack:
            opening = stack.pop()
            ranges.append((opening.start(), match.end()))
    return ranges


def _raw_tikz_drawing_matches(text: str) -> list[NativeDrawingMatch]:
    masked = _visual_scan_mask(text)
    range_ends = dict(_environment_ranges(masked, "tikzpicture"))
    drawings: list[NativeDrawingMatch] = []
    for match in re.finditer(r"\\begin\s*\{tikzpicture\}", masked):
        cursor = _skip_space(masked, match.end())
        option_group = None
        if cursor < len(masked) and masked[cursor] == "[":
            end = find_matching(masked, cursor, "[", "]")
            if end is not None:
                option_group = (cursor, end)
        drawings.append(
            NativeDrawingMatch(
                start=match.start(),
                end=range_ends.get(match.start(), match.end()),
                option_insert=match.end(),
                option_group=option_group,
                object_name="TikZ diagram",
                kind="tikz",
            )
        )

    for match in re.finditer(r"\\tikz(?![A-Za-z@])", masked):
        option_insert = match.end()
        cursor = _skip_space(masked, match.end())
        if cursor < len(masked) and masked[cursor] == "<":
            overlay_end = find_matching(masked, cursor, "<", ">")
            if overlay_end is None:
                continue
            option_insert = overlay_end
            cursor = _skip_space(masked, overlay_end)
        option_group = None
        if cursor < len(masked) and masked[cursor] == "[":
            end = find_matching(masked, cursor, "[", "]")
            if end is not None:
                option_group = (cursor, end)
                cursor = _skip_space(masked, end)
        body_end = (
            find_matching(masked, cursor, "{", "}")
            if cursor < len(masked) and masked[cursor] == "{"
            else None
        )
        if body_end is None:
            continue
        drawings.append(
            NativeDrawingMatch(
                start=match.start(),
                end=body_end,
                option_insert=option_insert,
                option_group=option_group,
                object_name="inline TikZ graphic",
                kind="tikz",
            )
        )
    return sorted(drawings, key=lambda item: item.start)


def _raw_picture_drawing_matches(text: str) -> list[NativeDrawingMatch]:
    masked = _visual_scan_mask(text)
    range_ends = dict(_environment_ranges(masked, "picture"))
    drawings: list[NativeDrawingMatch] = []
    for match in re.finditer(r"\\begin\s*\{picture\}", masked):
        cursor = _skip_space(masked, match.end())
        option_group = None
        if cursor < len(masked) and masked[cursor] == "[":
            end = find_matching(masked, cursor, "[", "]")
            if end is not None:
                option_group = (cursor, end)
        drawings.append(
            NativeDrawingMatch(
                start=match.start(),
                end=range_ends.get(match.start(), match.end()),
                option_insert=match.end(),
                option_group=option_group,
                object_name="picture environment",
                kind="picture",
            )
        )
    return drawings


def _drawing_options(text: str, drawing: NativeDrawingMatch) -> str:
    if drawing.option_group is None:
        return ""
    return text[drawing.option_group[0] + 1 : drawing.option_group[1] - 1]


def _drawing_suppresses_children(text: str, drawing: NativeDrawingMatch) -> bool:
    mode, _ = _annotation_mode(_drawing_options(text, drawing), kind=drawing.kind)
    # An unannotated outer drawing will receive an alt placeholder in this run.
    # Native text mode is the sole mode whose internal structures stay exposed.
    return mode != "text"


def _raw_unsupported_visual_environments(
    text: str,
) -> list[UnsupportedVisualEnvironmentMatch]:
    """Return exact, balanced unsupported visual environment ranges."""

    masked = _visual_scan_mask(text)
    environments: list[UnsupportedVisualEnvironmentMatch] = []
    for name in UNSUPPORTED_VISUAL_ENVIRONMENTS:
        range_ends = dict(_environment_ranges(masked, name))
        pattern = re.compile(r"\\begin\s*\{" + re.escape(name) + r"\}")
        for match in pattern.finditer(masked):
            environments.append(
                UnsupportedVisualEnvironmentMatch(
                    start=match.start(),
                    end=range_ends.get(match.start(), match.end()),
                    name=name,
                )
            )
    return environments


def _owned_visual_objects(
    text: str,
) -> list[NativeDrawingMatch | UnsupportedVisualEnvironmentMatch]:
    """Return top-level visual owners, exposing children only in native text mode."""

    candidates: list[NativeDrawingMatch | UnsupportedVisualEnvironmentMatch] = (
        _raw_tikz_drawing_matches(text)
        + _raw_picture_drawing_matches(text)
        + _raw_unsupported_visual_environments(text)
    )
    owned: list[NativeDrawingMatch | UnsupportedVisualEnvironmentMatch] = []
    for visual in sorted(candidates, key=lambda item: (item.start, -item.end)):
        hidden = False
        for outer in owned:
            suppresses_children = isinstance(
                outer,
                UnsupportedVisualEnvironmentMatch,
            ) or _drawing_suppresses_children(text, outer)
            if (
                suppresses_children
                and outer.start < visual.start
                and visual.end <= outer.end
            ):
                hidden = True
                break
        if not hidden:
            owned.append(visual)
    return owned


def _owned_native_drawings(text: str) -> list[NativeDrawingMatch]:
    """Return drawings not hidden inside an outer non-text visual object."""

    return [
        visual
        for visual in _owned_visual_objects(text)
        if isinstance(visual, NativeDrawingMatch)
    ]


def _owned_unsupported_visual_environments(
    text: str,
) -> list[UnsupportedVisualEnvironmentMatch]:
    return [
        visual
        for visual in _owned_visual_objects(text)
        if isinstance(visual, UnsupportedVisualEnvironmentMatch)
    ]


def _owned_visual_suppressing_ranges(text: str) -> list[tuple[int, int]]:
    return [
        (visual.start, visual.end)
        for visual in _owned_visual_objects(text)
        if isinstance(visual, UnsupportedVisualEnvironmentMatch)
        or _drawing_suppresses_children(text, visual)
    ]


def _tikz_drawing_matches(text: str) -> list[NativeDrawingMatch]:
    return [drawing for drawing in _owned_native_drawings(text) if drawing.kind == "tikz"]


def _picture_drawing_matches(text: str) -> list[NativeDrawingMatch]:
    return [
        drawing for drawing in _owned_native_drawings(text) if drawing.kind == "picture"
    ]


def _materialize_drawing_map_directives(
    text: str,
    relative: Path,
    config: ToolConfig,
) -> str:
    """Resolve configured maps before any directive insertion can shift lines."""

    replacements = _graphic_map_replacements(text, relative, config)
    for drawing in _owned_native_drawings(text):
        number = line_number(text, drawing.start)
        mapped = False
        alt: str | None = None
        for key in (
            f"{relative.as_posix()}::{drawing.kind}:{number}",
            f"{drawing.kind}:{number}",
        ):
            if key in config.image_alts:
                mapped, alt = True, config.image_alts[key]
                break
        if not mapped or (alt is not None and not alt.strip()):
            continue

        directive = _preceding_alt_directive_record(text, drawing.start)
        if directive is not None:
            existing_placeholder = (
                _placeholder_label(directive.alt, drawing.kind)
                if directive.alt is not None
                else None
            )
            mapped_placeholder = (
                _placeholder_label(alt, drawing.kind) if alt is not None else None
            )
            if existing_placeholder and not mapped_placeholder:
                replacements.append(_directive_replacement(directive, alt))
            continue

        mode, native_value = _annotation_mode(
            _drawing_options(text, drawing),
            kind=drawing.kind,
        )
        if mode == "alt" and _placeholder_label(native_value, drawing.kind):
            replacements.append(_directive_insertion(text, drawing.start, alt))
        elif mode is None:
            replacements.append(_directive_insertion(text, drawing.start, alt))
    return apply_replacements(text, replacements)


def _graphic_map_replacements(
    text: str,
    relative: Path,
    config: ToolConfig,
) -> list[tuple[int, int, str]]:
    """Collect durable graphic-map directives without changing line offsets."""

    replacements: list[tuple[int, int, str]] = []
    owned_ranges = [
        (drawing.start, drawing.end)
        for drawing in _owned_native_drawings(text)
        if _drawing_suppresses_children(text, drawing)
    ]
    for graphic in iter_graphic_commands(text):
        if any(start <= graphic.start < end for start, end in owned_ranges):
            continue
        asset = (group_content(text, graphic.mandatory) or "").strip()
        mapped, alt = _lookup_alt(config, relative, asset)
        if not mapped or (alt is not None and not alt.strip()):
            continue
        directive = _preceding_alt_directive_record(text, graphic.start)
        if directive is not None:
            existing_placeholder = (
                _placeholder_label(directive.alt, "graphic")
                if directive.alt is not None
                else None
            )
            mapped_placeholder = _placeholder_label(alt, "graphic") if alt else None
            if existing_placeholder and not mapped_placeholder:
                replacements.append(_directive_replacement(directive, alt))
            continue
        options = ",".join(
            group_content(text, group) or "" for group in graphic.optionals
        )
        mode, native_value = _annotation_mode(options, kind="graphic")
        if mode == "alt" and _placeholder_label(native_value, "graphic"):
            replacements.append(_directive_insertion(text, graphic.start, alt))
        elif mode is None:
            replacements.append(_directive_insertion(text, graphic.start, alt))
    return replacements


def _transform_native_drawings(
    text: str,
    relative: Path,
    config: ToolConfig,
    *,
    kind: str,
    drawings: list[NativeDrawingMatch],
    placeholder_allocator: PlaceholderAllocator,
    stateful_default: bool = False,
) -> tuple[str, list[Finding]]:
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for drawing in drawings:
        options = _drawing_options(text, drawing)
        if _handle_existing_annotation(
            text=text,
            offset=drawing.start,
            option_group=drawing.option_group,
            options=options,
            kind=kind,
            object_name=drawing.object_name,
            code=kind,
            relative=relative,
            config=config,
            placeholder_allocator=placeholder_allocator,
            replacements=replacements,
            findings=findings,
        ):
            continue

        number = line_number(text, drawing.start)
        directive = _preceding_alt_directive_record(text, drawing.start)
        found, alt = directive is not None, directive.alt if directive else None
        source = "directive"
        if not found:
            exact = f"{relative.as_posix()}::{kind}:{number}"
            generic = f"{kind}:{number}"
            for key in (exact, generic):
                if key in config.image_alts:
                    found, alt, source = True, config.image_alts[key], "map"
                    break
        if not found and stateful_default:
            findings.append(
                Finding(
                    kind + "-stateful-annotation",
                    f"{drawing.object_name.capitalize()} may inherit a stateful accessibility setting; add a local allytex directive or native option before placeholder automation.",
                    "error",
                    str(relative),
                    number,
                )
            )
            continue
        if not found:
            alt = _caption_for_graphic(text, drawing.start)
            found = alt is not None
            source = "caption"
        if not found:
            alt = _adjacent_drawing_description(text, drawing.end)
            found = alt is not None
            source = "adjacent description"
        if found and alt is not None and not alt.strip():
            found = False

        placeholder = bool(
            found
            and alt is not None
            and _placeholder_label(alt, kind) is not None
        )
        if not found:
            alt = placeholder_allocator.allocate(kind)
            found = True
            source = "generated placeholder"
            placeholder = True
        if source != "directive":
            replacements.append(_directive_insertion(text, drawing.start, alt))

        new_option = (
            "artifact"
            if alt is None
            else "alt={" + escape_tex_text(_normalize_alt_text(alt)) + "}"
        )
        if drawing.option_group is not None:
            start, end = drawing.option_group
            replacement = "[" + new_option + ("," + options if options.strip() else "") + "]"
        else:
            start = end = drawing.option_insert
            replacement = "[" + new_option + "]"
        replacements.append((start, end, replacement))

        if placeholder:
            findings.append(
                _placeholder_finding(
                    code=kind + "-placeholder-alt",
                    label=alt or kind,
                    object_name=drawing.object_name,
                    relative=relative,
                    line=number,
                    strict=config.strict,
                    inserted=True,
                )
            )
        else:
            findings.append(
                Finding(
                    kind + "-alt",
                    (
                        f"Tagged {drawing.object_name} as decorative."
                        if alt is None
                        else f"Added {drawing.object_name} alternative text from {source}."
                    ),
                    "info",
                    str(relative),
                    number,
                )
            )
    return apply_replacements(text, replacements), findings


def transform_tikz(
    text: str,
    relative: Path,
    config: ToolConfig,
    placeholder_allocator: PlaceholderAllocator | None = None,
) -> tuple[str, list[Finding]]:
    """Annotate TikZ pictures/commands with native alternatives or placeholders."""

    allocator = placeholder_allocator or PlaceholderAllocator.from_sources(
        [text],
        [value for value in config.image_alts.values() if value is not None],
    )
    return _transform_native_drawings(
        text,
        relative,
        config,
        kind="tikz",
        drawings=_tikz_drawing_matches(text),
        placeholder_allocator=allocator,
        stateful_default=(
            config.stateful_tikz_default
            or _has_stateful_native_default(text, "tikz")
        ),
    )


def transform_pictures(
    text: str,
    relative: Path,
    config: ToolConfig,
    placeholder_allocator: PlaceholderAllocator | None = None,
) -> tuple[str, list[Finding]]:
    """Annotate standard LaTeX picture environments through their native key."""

    allocator = placeholder_allocator or PlaceholderAllocator.from_sources(
        [text],
        [value for value in config.image_alts.values() if value is not None],
    )
    return _transform_native_drawings(
        text,
        relative,
        config,
        kind="picture",
        drawings=_picture_drawing_matches(text),
        placeholder_allocator=allocator,
        stateful_default=(
            config.stateful_graphic_default
            or _has_stateful_native_default(text, "graphic")
        ),
    )


def _table_body_start(masked: str, cursor: int, environment: str) -> int | None:
    """Return the body start after a common table environment's arguments."""

    def consume_group(index: int, opener: str, closer: str) -> int | None:
        index = _skip_space(masked, index)
        if index >= len(masked) or masked[index] != opener:
            return None
        return find_matching(masked, index, opener, closer)

    cursor = _skip_space(masked, cursor)
    if environment in {"tabular", "longtable"}:
        if cursor < len(masked) and masked[cursor] == "[":
            cursor = consume_group(cursor, "[", "]") or -1
        if cursor < 0:
            return None
        return consume_group(cursor, "{", "}")

    width_end = consume_group(cursor, "{", "}")
    if width_end is None:
        return None
    cursor = _skip_space(masked, width_end)
    if cursor < len(masked) and masked[cursor] == "[":
        cursor = consume_group(cursor, "[", "]") or -1
    if cursor < 0:
        return None
    return consume_group(cursor, "{", "}")


def _table_rows(
    text: str,
    masked: str,
    start: int,
    end: int,
) -> list[str]:
    """Return top-level rows separated by ordinary active ``\\`` commands."""

    rows: list[str] = []
    row_start = start
    depth = 0
    index = start
    while index < end:
        if _escaped(masked, index):
            index += 1
            continue
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif (
            depth == 0
            and char == "\\"
            and text[index : index + 2] == r"\\"
        ):
            rows.append(text[row_start:index])
            row_start = index + 2
            index += 1
        index += 1
    tail = text[row_start:end]
    if mask_tex(_strip_leading_table_rules(tail)).strip():
        rows.append(tail)
    return rows


def _strip_leading_table_rules(row: str) -> str:
    rule = re.compile(
        r"^\s*\\(?:hline|toprule|midrule|bottomrule)(?![A-Za-z@])"
    )
    while True:
        match = rule.match(row)
        if match is None:
            return row
        row = row[match.end() :]


def _split_table_cells(row: str) -> list[str]:
    masked = mask_tex(row)
    cells: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(masked):
        if _escaped(masked, index):
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "&" and depth == 0:
            cells.append(row[start:index])
            start = index + 1
    cells.append(row[start:])
    return cells


def _multicolumn_header_span(cell: str) -> int | None:
    """Return a pure, nonempty multicolumn header's declared span."""

    masked = mask_tex(cell)
    match = re.search(r"\\multicolumn(?![A-Za-z@])", masked)
    if match is None or masked[: match.start()].strip():
        return None
    cursor = match.end()
    groups: list[tuple[int, int]] = []
    for _ in range(3):
        cursor = _skip_space(masked, cursor)
        if cursor >= len(masked) or masked[cursor] != "{":
            return None
        group_end = find_matching(masked, cursor, "{", "}")
        if group_end is None:
            return None
        groups.append((cursor, group_end))
        cursor = group_end
    if masked[cursor:].strip():
        return None
    raw_span = group_content(cell, groups[0]) or ""
    heading = group_content(masked, groups[2]) or ""
    if not raw_span.strip().isdigit() or not heading.strip():
        return None
    span = int(raw_span.strip())
    return span if span > 1 else None


def _uses_grouped_row_and_column_headers(
    text: str,
    masked: str,
    opening: re.Match[str],
    closing: re.Match[str],
    environment: str,
) -> bool:
    """Recognize a conservative two-tier row/column-header table."""

    # Longtable has separate head/foot row commands whose semantics cannot be
    # established by this small source parser.
    if environment != "tabular":
        return False

    body_start = _table_body_start(masked, opening.end(), environment)
    if body_start is None:
        return False
    rows = _table_rows(text, masked, body_start, closing.start())
    if len(rows) < 3:
        return False
    if any(re.search(r"\\begin(?![A-Za-z@])", mask_tex(row)) for row in rows):
        return False

    first_cells = _split_table_cells(_strip_leading_table_rules(rows[0]))
    second_cells = _split_table_cells(_strip_leading_table_rules(rows[1]))
    if len(first_cells) < 2 or len(second_cells) < 3:
        return False
    if mask_tex(first_cells[0]).strip():
        return False
    group_spans = [_multicolumn_header_span(cell) for cell in first_cells[1:]]
    if any(span is None for span in group_spans):
        return False
    if not all(mask_tex(cell).strip() for cell in second_cells):
        return False
    if any(
        re.search(r"\\multicolumn(?![A-Za-z@])", mask_tex(cell))
        for cell in second_cells
    ):
        return False

    width = len(second_cells)
    if 1 + sum(span for span in group_spans if span is not None) != width:
        return False

    complex_body_cell = re.compile(
        r"\\(?:multicolumn|multirow|omit|span)(?![A-Za-z@])"
    )
    for row in rows[2:]:
        cleaned = _strip_leading_table_rules(row)
        row_mask = mask_tex(cleaned)
        if complex_body_cell.search(row_mask):
            return False
        cells = _split_table_cells(cleaned)
        if len(cells) != width or not mask_tex(cells[0]).strip():
            return False
    return True


def transform_tables(
    text: str,
    relative: Path,
    config: ToolConfig,
) -> tuple[str, list[Finding]]:
    """Scope the first logical row as column headers for common table environments."""

    if config.table_headers == "manual":
        return text, []
    masked = mask_tex(text)
    names = "|".join(re.escape(name) for name in sorted(TABLE_ENVIRONMENTS, key=len, reverse=True))
    pattern = re.compile(r"\\(begin|end)\s*\{(" + names + r")\}")
    stack: list[tuple[str, re.Match[str]]] = []
    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for match in pattern.finditer(masked):
        kind, name = match.group(1), match.group(2)
        if kind == "begin":
            stack.append((name, match))
            continue
        if not stack:
            continue
        open_name, opening = stack.pop()
        if open_name != name:
            continue
        # Avoid double instrumentation on repeated runs or hand-tagged tables.
        prefix = text[max(0, opening.start() - 120) : opening.start()]
        if "table/header-rows" in prefix:
            continue
        grouped_headers = _uses_grouped_row_and_column_headers(
            text,
            masked,
            opening,
            match,
            name,
        )
        table_setup = (
            r"table/header-rows={1,2},table/header-columns={1}"
            if grouped_headers
            else r"table/header-rows={1}"
        )
        replacements.append(
            (
                opening.start(),
                opening.start(),
                rf"\begingroup\tagpdfsetup{{{table_setup}}}",
            )
        )
        replacements.append((match.end(), match.end(), r"\endgroup"))
        if grouped_headers:
            message = (
                f"Detected grouped headings in {name}; tagged its first two "
                "rows as column headers and its first column as row headers."
            )
        else:
            message = (
                f"Tagged the first row of {name} as column headers; verify "
                "multi-row or layout tables manually."
            )
        findings.append(
            Finding(
                "table-header",
                message,
                "warning",
                str(relative),
                line_number(text, opening.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def transform_visual_layout_tables(
    text: str,
    relative: Path,
) -> tuple[str, list[Finding]]:
    """Flatten a provably one-row, graphics-only tabular used for layout."""

    masked = mask_tex(text)
    token = re.compile(r"\\(begin|end)\s*\{tabular\}")
    stack: list[re.Match[str]] = []
    pairs: list[tuple[re.Match[str], re.Match[str]]] = []
    for match in token.finditer(masked):
        if match.group(1) == "begin":
            stack.append(match)
        elif stack:
            pairs.append((stack.pop(), match))

    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for opening, closing in pairs:
        cursor = _skip_space(masked, opening.end())
        if cursor >= len(masked) or masked[cursor] != "{":
            continue
        columns_end = find_matching(masked, cursor, "{", "}")
        if columns_end is None:
            continue
        body_start = columns_end
        body_end = closing.start()
        body = text[body_start:body_end]
        body_mask = mask_tex(body)
        graphics = iter_graphic_commands(body)
        if not graphics or r"\\" in body_mask:
            continue
        residue = list(body_mask)
        for graphic in graphics:
            for index in range(graphic.start, min(graphic.end, len(residue))):
                if residue[index] not in "\r\n":
                    residue[index] = " "
        if re.sub(r"[\s&]", "", "".join(residue)):
            continue
        column_separators = [
            index
            for index, char in enumerate(body_mask)
            if char == "&"
            and not _escaped(body_mask, index)
            and not any(
                graphic.start <= index < graphic.end for graphic in graphics
            )
        ]
        flattened = apply_replacements(
            body,
            [(index, index + 1, r"\hfill") for index in column_separators],
        )
        replacements.append(
            (
                opening.start(),
                closing.end(),
                r"\begingroup\centering"
                + flattened
                + r"\par\endgroup",
            )
        )
        findings.append(
            Finding(
                "visual-layout-table-flattened",
                "Converted a one-row graphics-only tabular to a non-table layout group so it is not exposed as a data table.",
                "info",
                str(relative),
                line_number(text, opening.start()),
            )
        )
    return apply_replacements(text, replacements), findings


def _visual_reference_in_definition(
    masked: str,
    definition: VisualDefinitionMatch,
) -> str | None:
    """Name the first renderer hidden inside a protected definition body."""

    direct_names = tuple(
        sorted(
            {"includegraphics", "tikz", *UNSUPPORTED_VISUAL_COMMANDS},
            key=len,
            reverse=True,
        )
    )
    direct_pattern = re.compile(
        r"\\(" + "|".join(map(re.escape, direct_names)) + r")(?![A-Za-z@])"
    )
    dynamic_pattern = re.compile(
        r"\\csname\s*(" + "|".join(map(re.escape, DYNAMIC_VISUAL_COMMANDS))
        + r")\s*\\endcsname"
    )
    environment_names = tuple(
        sorted(
            {"picture", "tikzpicture", *UNSUPPORTED_VISUAL_ENVIRONMENTS},
            key=len,
            reverse=True,
        )
    )
    environment_pattern = re.compile(
        r"\\begin\s*\{(" + "|".join(map(re.escape, environment_names)) + r")\}"
    )
    candidates: list[tuple[int, str]] = []
    for start, end in definition.content_ranges:
        body = masked[start:end]
        body = reduce_literal_conditionals(
            body,
            token_mask=_mask_visual_definition_regions(body),
        )
        body = _mask_visual_definition_regions(body)
        for match in direct_pattern.finditer(body):
            candidates.append((start + match.start(), "\\" + match.group(1)))
        for match in dynamic_pattern.finditer(body):
            candidates.append(
                (start + match.start(), "dynamic \\" + match.group(1))
            )
        for match in environment_pattern.finditer(body):
            candidates.append(
                (start + match.start(), match.group(1) + " environment")
            )
    return min(candidates)[1] if candidates else None


def find_unhandled_structural_content(
    text: str,
    relative: Path,
    config: ToolConfig,
) -> list[Finding]:
    """Block visual/table constructs for which this tool cannot add honest tags."""

    active_lexical = _active_visual_lexical_mask(text)
    masked = _mask_visual_definition_regions(active_lexical)
    findings: list[Finding] = []
    for definition in _visual_definition_matches(active_lexical):
        identifier = _definition_identifier(active_lexical, definition)
        if (
            config.reachable_visual_definitions is not None
            and identifier is not None
            and identifier not in config.reachable_visual_definitions
        ):
            continue
        reference = _visual_reference_in_definition(active_lexical, definition)
        if reference is None:
            continue
        findings.append(
            Finding(
                "unsupported-visual-in-definition",
                f"A macro or environment definition wraps {reference}; its rendered calls cannot be located and annotated safely. Add verified tagging inside the wrapper or replace it with directly supported visual commands.",
                "error",
                str(relative),
                line_number(text, definition.start),
            )
        )
    covered_visual_ranges = _owned_visual_suppressing_ranges(text)
    for command, message in UNSUPPORTED_VISUAL_COMMANDS.items():
        for match in re.finditer(r"\\" + command + r"(?![A-Za-z@])", masked):
            if any(
                start < match.start() < end
                for start, end in covered_visual_ranges
            ):
                continue
            findings.append(
                Finding(
                    "unsupported-visual-" + command,
                    message,
                    "error",
                    str(relative),
                    line_number(text, match.start()),
                )
            )
    dynamic_visual_pattern = re.compile(
        r"\\csname\s*(" + "|".join(map(re.escape, DYNAMIC_VISUAL_COMMANDS))
        + r")\s*\\endcsname"
    )
    for match in dynamic_visual_pattern.finditer(masked):
        if any(
            start < match.start() < end
            for start, end in covered_visual_ranges
        ):
            continue
        findings.append(
            Finding(
                "unsupported-dynamic-visual-command",
                f"Dynamically formed \\{match.group(1)} cannot be given a verified native alternative automatically; use the direct command spelling.",
                "error",
                str(relative),
                line_number(text, match.start()),
            )
        )
    parsed_inline_tikz = {
        drawing.start
        for drawing in _raw_tikz_drawing_matches(text)
        if drawing.object_name == "inline TikZ graphic"
    }
    for match in re.finditer(r"\\tikz(?![A-Za-z@])", masked):
        if match.start() in parsed_inline_tikz or any(
            start < match.start() < end for start, end in covered_visual_ranges
        ):
            continue
        prefix = masked[max(0, match.start() - 2048) : match.start()]
        if re.search(
            r"(?:\\global\s*)?\\(?:let|futurelet)\s*\\(?:[A-Za-z@]+|.)\s*=?\s*$",
            prefix,
        ):
            continue
        cursor = _skip_space(masked, match.end())
        if cursor >= len(masked) or masked[cursor] in "}]":
            continue
        findings.append(
            Finding(
                "unsupported-inline-tikz-form",
                "Brace-less or dynamically formed \\tikz content cannot be bounded safely; wrap the drawing body in braces so a native alt option can be added.",
                "error",
                str(relative),
                line_number(text, match.start()),
            )
        )
    for environment in _owned_unsupported_visual_environments(text):
        findings.append(
            Finding(
                "unsupported-environment-" + environment.name,
                UNSUPPORTED_VISUAL_ENVIRONMENTS[environment.name],
                "error",
                str(relative),
                line_number(text, environment.start),
            )
        )

    unsupported_table_environments = {
        "tblr": "tabularray tables are not auto-instrumented; add headers manually and use --table-headers manual.",
        "longtblr": "tabularray tables are not auto-instrumented; add headers manually and use --table-headers manual.",
        "tabu": "tabu tables are not auto-instrumented; convert them to a supported table or tag headers manually.",
    }
    for environment, message in unsupported_table_environments.items():
        for match in re.finditer(
            r"\\begin\s*\{" + re.escape(environment) + r"\}",
            masked,
        ):
            severity = (
                "warning"
                if config.table_headers == "manual"
                else "error"
            )
            findings.append(
                Finding(
                    "unsupported-environment-" + environment,
                    message,
                    severity,
                    str(relative),
                    line_number(text, match.start()),
                )
            )
    return findings


def transform_unhandled_visual_directives(
    text: str,
    relative: Path,
    config: ToolConfig,
    placeholder_allocator: PlaceholderAllocator | None = None,
) -> tuple[str, list[Finding]]:
    """Add durable review markers to visuals without a safe native rewrite.

    The markers intentionally do not suppress the corresponding blocking
    findings: a source comment cannot create an accessible PDF structure.
    """

    allocator = placeholder_allocator or PlaceholderAllocator.from_sources([text])
    masked = _visual_scan_mask(text)
    covered_visual_ranges = _owned_visual_suppressing_ranges(text)
    targets: list[tuple[int, str]] = []
    for command in UNSUPPORTED_VISUAL_COMMANDS:
        for match in re.finditer(r"\\" + command + r"(?![A-Za-z@])", masked):
            if any(
                start < match.start() < end
                for start, end in covered_visual_ranges
            ):
                continue
            targets.append((match.start(), command))
    dynamic_visual_pattern = re.compile(
        r"\\csname\s*(" + "|".join(map(re.escape, DYNAMIC_VISUAL_COMMANDS))
        + r")\s*\\endcsname"
    )
    for match in dynamic_visual_pattern.finditer(masked):
        if any(
            start < match.start() < end
            for start, end in covered_visual_ranges
        ):
            continue
        targets.append((match.start(), f"dynamic \\{match.group(1)} command"))
    for environment in _owned_unsupported_visual_environments(text):
        targets.append(
            (environment.start, environment.name + " environment")
        )

    replacements: list[tuple[int, int, str]] = []
    findings: list[Finding] = []
    for offset, object_name in sorted(set(targets)):
        directive = _preceding_alt_directive_record(text, offset)
        if directive is None:
            label = allocator.allocate("visual")
            replacements.append(_directive_insertion(text, offset, label))
            inserted = True
        elif directive.alt is not None and _placeholder_label(
            directive.alt,
            "visual",
        ):
            label = directive.alt.strip().lower()
            inserted = False
        else:
            continue
        findings.append(
            _placeholder_finding(
                code="visual-placeholder-alt",
                label=label,
                object_name=object_name,
                relative=relative,
                line=line_number(text, offset),
                strict=config.strict,
                inserted=inserted,
            )
        )
    return apply_replacements(text, replacements), findings


def transform_tex_file(
    path: Path,
    root: Path,
    config: ToolConfig,
    *,
    is_main: bool,
    migrate_beamer: bool = False,
    preprocess_presentation: bool = False,
    active_end_offset: int | None = None,
    placeholder_allocator: PlaceholderAllocator | None = None,
) -> tuple[str | None, list[Finding], str | None, str | None]:
    """Transform one copied TeX file, returning content only when it changed."""

    original = path.read_text(encoding="utf-8")
    if active_end_offset is not None:
        text = original[:active_end_offset]
        trailing = original[active_end_offset:]
    else:
        text = original
        trailing = ""
    relative = path.relative_to(root)
    findings: list[Finding] = []
    profile = None
    title = None
    if migrate_beamer or preprocess_presentation:
        text, preprocessing_findings = _preprocess_beamer_content(
            text,
            relative,
            config.beamer_target_mode,
            strict=config.strict,
        )
        findings.extend(preprocessing_findings)
    if preprocess_presentation:
        text, underlined_math_findings = transform_underlined_math(
            text,
            relative,
            strict=config.strict,
        )
        findings.extend(underlined_math_findings)
    text, theorem_citation_findings = transform_theorem_note_citations(
        text,
        relative,
        config.tagged_theorem_environments,
    )
    findings.extend(theorem_citation_findings)
    text = _materialize_drawing_map_directives(text, relative, config)
    text, visual_directive_findings = transform_unhandled_visual_directives(
        text,
        relative,
        config,
        placeholder_allocator,
    )
    findings.extend(visual_directive_findings)
    findings.extend(find_unhandled_structural_content(text, relative, config))
    text, graphic_findings = transform_graphics(
        text,
        relative,
        config,
        placeholder_allocator,
    )
    findings.extend(graphic_findings)
    text, tikz_findings = transform_tikz(
        text,
        relative,
        config,
        placeholder_allocator,
    )
    findings.extend(tikz_findings)
    text, picture_findings = transform_pictures(
        text,
        relative,
        config,
        placeholder_allocator,
    )
    findings.extend(picture_findings)
    text, layout_table_findings = transform_visual_layout_tables(text, relative)
    findings.extend(layout_table_findings)
    text, table_findings = transform_tables(text, relative, config)
    findings.extend(table_findings)
    if is_main:
        text, profile, main_findings, title = instrument_main(text, relative, config)
        findings.extend(main_findings)
    elif migrate_beamer:
        text, migration_findings = migrate_beamer_content(text, relative, config)
        findings.extend(migration_findings)
    text += trailing
    return (text if text != original else None), findings, profile, title
