from __future__ import annotations

import hashlib
import re
from importlib.resources import files


def load_system_prompt() -> tuple[str, str, str]:
    """Return (prompt_text, version, sha256[:12]). The version comes from the file header."""
    text = files("gitrescue.prompts").joinpath("system.md").read_text(encoding="utf-8")
    m = re.search(r"^<!--\s*version:\s*([\w.\-]+)\s*-->", text, re.M)
    version = m.group(1) if m else "unversioned"
    body = re.sub(r"^<!--.*?-->\s*", "", text, count=1, flags=re.S)
    return body, version, hashlib.sha256(text.encode()).hexdigest()[:12]
