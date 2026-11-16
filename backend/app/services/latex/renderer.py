"""
app.services.latex.renderer
---------------------------
Deterministic rendering of a validated :class:`TailoredResume` into the
LaTeX source of the resume (``templates/resume.tex.j2``, derived from the
apex resume template).

Pure function, no I/O beyond loading the packaged template, no timestamps:
the same input always yields byte-identical output.

Every value is escaped before it reaches LaTeX, so resume content can never
inject LaTeX commands (``\\input``, ``\\write18`` …). The only markup the
content may carry is ``**bold**``, converted to ``\\textbf{}`` after escaping.
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

import jinja2

from app.services.latex.layout import ResumeLayout, plan_layout
from app.services.latex.schemas import CertificationEntry, TailoredResume

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE_NAME = "resume.tex.j2"

# Characters with a special meaning in LaTeX text mode → their safe form.
_TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "<": r"\textless{}",
    ">": r"\textgreater{}",
    "|": r"\textbar{}",
}
_TEX_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _TEX_ESCAPES))
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def tex_escape(text: str) -> str:
    """
    Escape plain text for LaTeX and convert ``**bold**`` to ``\\textbf{}``.

    Control characters are dropped and runs of whitespace (incl. newlines)
    collapse to one space, so a value can never break the template's layout.
    """
    text = _CONTROL_CHARS_RE.sub("", str(text or ""))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _TEX_ESCAPE_RE.sub(lambda m: _TEX_ESCAPES[m.group(0)], text)
    # '*' is not special in LaTeX, so the markers survive escaping untouched.
    return _BOLD_RE.sub(r"\\textbf{\1}", text)


def tex_url(url: str) -> str:
    """Make a (validated) URL safe as the target argument of ``\\href``."""
    url = re.sub(r"[\\{}\s]", "", str(url or ""))
    return url.replace("%", r"\%").replace("#", r"\#")


def url_display(url: str) -> str:
    """Human-readable link text: no scheme, no ``www.``, no trailing slash."""
    text = re.sub(r"^https?://", "", str(url or ""), flags=re.IGNORECASE)
    text = re.sub(r"^www\.", "", text, flags=re.IGNORECASE).rstrip("/")
    return tex_escape(text)


def _date_range(start: str, end: str) -> str:
    """``START -- END``, or just whichever date is present."""
    if start and end:
        return f"{tex_escape(start)} -- {tex_escape(end)}"
    return tex_escape(start or end)


def _certification(c: CertificationEntry) -> str:
    """``name $|$ issuer $|$ year $|$ \\href{url}{Verify here}`` (blanks dropped)."""
    parts = [tex_escape(p) for p in (c.name, c.issuer, c.year) if p]
    if c.url:
        parts.append(rf"\href{{{tex_url(c.url)}}}{{\underline{{Verify here}}}}")
    return " $|$ ".join(parts)


def _contacts(resume: TailoredResume) -> list[str]:
    """The header's contact items (phone, email, LinkedIn, GitHub) that are present."""
    info = resume.personal_information
    items: list[str] = []
    if info.phone:
        tel = re.sub(r"[^\d+]", "", info.phone)
        items.append(
            rf"\href{{tel:{tel}}}{{\raisebox{{-0.1\height}}\faPhone\ \underline{{{tex_escape(info.phone)}}}}}"
        )
    if info.email:
        items.append(
            rf"\href{{mailto:{tex_url(info.email)}}}{{\raisebox{{-0.2\height}}\faEnvelope\ "
            rf"\underline{{{tex_escape(info.email)}}}}}"
        )
    if info.linkedin:
        items.append(
            rf"\href{{{tex_url(info.linkedin)}}}{{\raisebox{{-0.2\height}}\faLinkedin\ "
            rf"\underline{{{url_display(info.linkedin)}}}}}"
        )
    if info.github:
        items.append(
            rf"\href{{{tex_url(info.github)}}}{{\raisebox{{-0.2\height}}\faGithub\ "
            rf"\underline{{{url_display(info.github)}}}}}"
        )
    return items


@lru_cache(maxsize=1)
def _environment() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        block_start_string="((*",
        block_end_string="*))",
        variable_start_string="(((",
        variable_end_string=")))",
        comment_start_string="((=",
        comment_end_string="=))",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["tex"] = tex_escape
    env.filters["texurl"] = tex_url
    env.filters["urldisplay"] = url_display
    env.globals["daterange"] = _date_range
    env.globals["certification"] = _certification
    return env


def render_resume_latex(
    resume: TailoredResume,
    today: Optional[date] = None,
    layout: Optional[ResumeLayout] = None,
) -> str:
    """
    Render the resume's complete LaTeX source.

    Section order and the shown experience entries follow
    :func:`app.services.latex.layout.plan_layout` (early-career order when
    total work experience is under one year; internships only when there is
    no actual work experience).

    Args:
        resume: Validated resume content.
        today: Reference date for "PRESENT" when computing total experience
            (defaults to today). Ignored when ``layout`` is given.
        layout: A layout already planned for this resume.

    Returns:
        The ``.tex`` document as a string (deterministic for a given input
        and reference date).
    """
    layout = layout or plan_layout(resume, today)
    template = _environment().get_template(_TEMPLATE_NAME)
    return template.render(r=resume, layout=layout, contacts=_contacts(resume))
