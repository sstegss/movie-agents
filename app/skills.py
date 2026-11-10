"""Загрузка навыков (skills) и сборка системных промптов.

Навык это папка со SKILL.md: в заголовке YAML с именем, описанием,
агентом и инструментами, ниже инструкция. Рядом могут лежать справочные
файлы (references), они подклеиваются к инструкции.

Загрузка постепенная, как у Agent Skills: в каталоге навыков видны
только имена и описания, полный текст попадает в промпт, только когда
агент действительно выполняет эту задачу. Так маленькая модель не тратит
контекст на чужие инструкции.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings


@dataclass
class Skill:
    name: str
    description: str
    agent: str
    tools: list[str]
    body: str
    references: list[str] = field(default_factory=list)
    path: Path | None = None

    def full_text(self) -> str:
        parts = [self.body.strip()]
        for ref in self.references:
            ref_path = self.path.parent / ref
            if ref_path.exists():
                parts.append(ref_path.read_text().strip())
        return "\n\n".join(parts)


def parse_skill(path: Path) -> Skill:
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path}: нет заголовка YAML")
    _, header, body = text.split("---", 2)
    meta = yaml.safe_load(header)
    for key in ("name", "description", "agent"):
        if not meta.get(key):
            raise ValueError(f"{path}: в заголовке нет поля {key}")
    return Skill(
        name=meta["name"],
        description=meta["description"],
        agent=meta["agent"],
        tools=meta.get("tools") or [],
        body=body,
        references=meta.get("references") or [],
        path=path,
    )


@lru_cache(maxsize=1)
def load_skills(root: Path | None = None) -> dict[str, Skill]:
    root = root or settings.skills_dir
    skills = {}
    for path in sorted(root.glob("*/SKILL.md")):
        skill = parse_skill(path)
        skills[skill.name] = skill
    return skills


def skill_for_agent(agent: str) -> Skill | None:
    return next((s for s in load_skills().values() if s.agent == agent), None)


def skills_index() -> str:
    """Короткий каталог навыков: имя и описание."""
    return "\n".join(f"- {s.name}: {s.description}" for s in load_skills().values())


@lru_cache(maxsize=16)
def _read(name: str) -> str:
    return (settings.agent_dir / name).read_text().strip()


def system_prompt(agent: str, profile_text: str = "", extra: str = "") -> str:
    """SOUL.md + роль агента + его навык + профиль пользователя.

    Служебным агентам (память, валидатор) SOUL.md не нужен: с пользователем
    они не говорят, а контекст у маленьких моделей дорогой.
    """
    parts = [] if agent in ("memory", "validator") else [_read("SOUL.md")]
    parts.append("# Твоя роль\n" + _read(f"prompts/{agent}.md"))
    skill = skill_for_agent(agent)
    if skill:
        parts.append(f"# Навык: {skill.name}\n{skill.full_text()}")
    if profile_text:
        parts.append("# Что известно о пользователе\n" + profile_text)
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)
