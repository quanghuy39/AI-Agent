"""Skill bao cao: prompt (synthesize.md) va schema (schemas.py).

render_prompt dung string.Template.substitute: thieu bien thi KeyError.
"""

from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name):
    """Noi dung file <name>.md trong skills/report/."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Không có prompt '{name}' ({path})")
    return path.read_text(encoding="utf-8")


def render_prompt(name, **variables):
    """Dien bien vao prompt. Thieu bien -> KeyError."""
    return Template(load_prompt(name)).substitute(variables)
