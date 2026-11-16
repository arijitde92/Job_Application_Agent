"""Tests for the deterministic LaTeX renderer."""

from app.services.latex.renderer import render_resume_latex, tex_escape, tex_url, url_display
from app.services.latex.schemas import TailoredResume
from tests.latex_fixtures import sample_resume


def _render(data=None) -> str:
    return render_resume_latex(TailoredResume.model_validate(data or sample_resume()))


def test_escapes_every_special_character():
    assert tex_escape(r"a & b % c $ d # e _ f { g } h ~ i ^ j \ k") == (
        r"a \& b \% c \$ d \# e \_ f \{ g \} h \textasciitilde{} i "
        r"\textasciicircum{} j \textbackslash{} k"
    )


def test_latex_commands_in_content_are_inert():
    escaped = tex_escape(r"\input{/etc/passwd} \write18{rm -rf /}")
    assert "\\input{" not in escaped
    assert "\\write18{" not in escaped
    assert escaped.startswith(r"\textbackslash{}input\{")


def test_bold_markers_become_textbf_after_escaping():
    assert tex_escape("Cut cost by **35%** with **C#**") == (
        r"Cut cost by \textbf{35\%} with \textbf{C\#}"
    )


def test_whitespace_and_control_characters_are_collapsed():
    assert tex_escape("line one\n\nline\ttwo\x00\x07") == "line one line two"


def test_url_helpers():
    assert tex_url("https://x.com/a#b?c=1%20") == r"https://x.com/a\#b?c=1\%20"
    assert url_display("https://www.github.com/ada/Civil_RAG/") == r"github.com/ada/Civil\_RAG"


def test_render_is_deterministic():
    assert _render() == _render()


def test_render_contains_every_section_in_template_order():
    tex = _render()
    sections = [
        r"\section{Professional Summary}", r"\section{Work Experience}",
        r"\section{Education}", r"\section{Skills}", r"\section{Projects}",
        r"\section{Publications}", r"\section{Certifications}",
    ]
    positions = [tex.index(s) for s in sections]
    assert positions == sorted(positions)
    assert tex.startswith(r"\documentclass[letterpaper,11pt]{article}")
    assert tex.rstrip().endswith(r"\end{document}")


def test_render_maps_fields_into_the_template_macros():
    tex = _render()
    assert "{\\Huge \\scshape Ada Lovelace}" in tex
    assert "{Acme, Singapore}{SEP 2025 -- PRESENT}" in tex
    assert "{Senior AI Engineer}{Remote, Full time}" in tex
    assert r"\resumeItem{Built \textbf{LangGraph} agents, cutting token usage by 35\%.}" in tex
    assert r"{M.E. Computer Science \& Engineering \textbar{} GPA: 8.83}{}" in tex
    assert r"\textbf{Languages}{: Python, C++, SQL} \\" in tex
    assert r"\textbf{Cloud \& Ops}{: GCP, Docker}" in tex
    assert r"\href{https://github.com/ada/Civil_RAG}{\underline{github.com/ada/Civil\_RAG}}" in tex
    assert r"\href{tel:+442012345678}" in tex
    assert r"\href{mailto:ada@example.com}" in tex
    assert (
        r"Google AI Professional Certificate $|$ Google $|$ 2026 $|$ "
        r"\href{https://coursera.org/share/abc}{\underline{Verify here}}"
    ) in tex


def test_last_skill_line_has_no_line_break():
    tex = _render()
    assert r"\textbf{Cloud \& Ops}{: GCP, Docker} \\" not in tex


def test_empty_sections_are_omitted():
    data = sample_resume()
    for key in ("projects", "publications", "certifications", "education", "skills"):
        data[key] = []
    data["personal_information"].update(phone="", email="", linkedin="", github="", location="")
    tex = _render(data)
    for section in ("Projects", "Publications", "Certifications", "Education", "Skills"):
        assert f"\\section{{{section}}}" not in tex
    assert r"\faPhone" not in tex
    assert r"\section{Work Experience}" in tex


def test_pdftex_only_primitives_are_guarded():
    tex = _render()
    assert "\\ifPDFTeX\n  \\input{glyphtounicode}\n  \\pdfgentounicode=1\n\\fi" in tex
