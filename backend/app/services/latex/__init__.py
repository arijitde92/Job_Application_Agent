"""
app.services.latex
------------------
LaTeX resume generation: a Pydantic schema of the tailored resume content,
a deterministic Jinja2 renderer (``templates/resume.tex.j2``), a Tectonic
compiler wrapper, and a short-lived server-local cache of compiled PDFs.
"""

from app.services.latex.compiler import CompileResult, compile_latex_to_pdf
from app.services.latex.layout import ResumeLayout, plan_layout
from app.services.latex.renderer import render_resume_latex, tex_escape
from app.services.latex.schemas import TailoredResume

__all__ = [
    "CompileResult",
    "ResumeLayout",
    "TailoredResume",
    "compile_latex_to_pdf",
    "plan_layout",
    "render_resume_latex",
    "tex_escape",
]
