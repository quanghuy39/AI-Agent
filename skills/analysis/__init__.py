"""Skill phan tich: prompt (plan.md, step.md, contract.md) va schema (schemas.py).

render_prompt dung string.Template.substitute: thieu bien thi KeyError; $contract tu chen tu contract.md.
"""

from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name):
    """Noi dung file <name>.md trong skills/analysis/."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Không có prompt '{name}' ({path})")
    return path.read_text(encoding="utf-8")


def render_prompt(name, **variables):
    """Dien bien vao prompt. $contract tu chen (truyen contract=... de thay). Thieu bien -> KeyError."""
    variables.setdefault("contract", load_prompt("contract"))
    return Template(load_prompt(name)).substitute(variables)
