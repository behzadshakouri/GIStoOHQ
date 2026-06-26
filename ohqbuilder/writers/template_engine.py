from pathlib import Path
from string import Template

def render_template(path: Path, context: dict) -> str:
    return Template(path.read_text()).safe_substitute(context)
