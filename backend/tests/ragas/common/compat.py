"""
Compatibility shim: make `import ragas` work on the langchain 1.x line.

ragas 0.4.3 eagerly imports ``langchain_community.chat_models.vertexai`` at
``import ragas`` time, but that module was removed from langchain-community
0.4.x (the langchain 1.x line this project is on). ragas only uses the class
in an ``isinstance`` whitelist for multiple-completion support
(``ragas/llms/base.py::MULTIPLE_COMPLETION_SUPPORTED``) — a Vertex code path
this harness never exercises (the judge LLM is ``ChatGoogleGenerativeAI``) —
so a sentinel class no real object is an instance of is a safe stand-in.

Import this module before importing ragas. Importing
``tests.ragas.common`` does it automatically.
"""

import sys
import types

_SHIM_MODULE = "langchain_community.chat_models.vertexai"


def _install_shim() -> None:
    if _SHIM_MODULE in sys.modules:
        return
    try:
        __import__(_SHIM_MODULE)
        return  # real module exists (older langchain-community) — nothing to do
    except ModuleNotFoundError:
        pass

    module = types.ModuleType(_SHIM_MODULE)

    class ChatVertexAI:  # sentinel: only used in ragas' isinstance whitelist
        pass

    module.ChatVertexAI = ChatVertexAI
    sys.modules[_SHIM_MODULE] = module


_install_shim()
