"""Parse criteria.txt into structured criteria.

Format of criteria.txt:
    A. Section title
    1. Criterion title: Question text ...
"""
import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

CRITERIA_FILE = Path(__file__).resolve().parent.parent / 'criteria.txt'

SECTION_RE = re.compile(r'^([A-Z])\.\s+(.+)$')
CRITERION_RE = re.compile(r'^(\d+)\.\s+([^:]+):\s*(.+)$')


@dataclass(frozen=True)
class Criterion:
    id: int
    section: str
    section_title: str
    title: str
    question: str


@lru_cache(maxsize=1)
def load_criteria(path: Path = CRITERIA_FILE) -> tuple[Criterion, ...]:
    criteria = []
    section, section_title = None, None
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if match := SECTION_RE.match(line):
            section, section_title = match.group(1), match.group(2).strip()
        elif match := CRITERION_RE.match(line):
            if section is None:
                raise ValueError(f'Criterion before first section: {line}')
            criteria.append(Criterion(
                id=int(match.group(1)),
                section=section,
                section_title=section_title,
                title=match.group(2).strip(),
                question=match.group(3).strip(),
            ))
        else:
            raise ValueError(f'Unparseable line in {path.name}: {line}')
    return tuple(criteria)


def criteria_as_dicts() -> list[dict]:
    return [asdict(c) for c in load_criteria()]


def criteria_as_prompt_text() -> str:
    lines = []
    current_section = None
    for c in load_criteria():
        if c.section != current_section:
            current_section = c.section
            lines.append(f'\n{c.section}. {c.section_title}')
        lines.append(f'{c.id}. {c.title}: {c.question}')
    return '\n'.join(lines).strip()
