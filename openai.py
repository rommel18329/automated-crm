"""Local lightweight fallback for environments without the official openai package.

If the official package is installed, it should take precedence in site-packages when installed
as a dependency in real usage. This fallback keeps local tests runnable in restricted environments.
"""

from __future__ import annotations


class _Responses:
    def create(self, model: str, input: str):
        raise RuntimeError("OpenAI SDK unavailable in this environment")


class OpenAI:
    def __init__(self):
        self.responses = _Responses()
