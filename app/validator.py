"""Слой верификации ответов агентов.

Два уровня:
1. Детерминированные проверки по каталогу (дёшево, без LLM): каждый фильм
   из ответа должен существовать в каталоге и прийти из инструментов,
   рекомендации не должны включать то, что человек не любит или видел.
2. LLM-судья на модели другого семейства (Gemma против Qwen): отвечает ли
   ответ на запрос и не противоречит ли профилю. Другое семейство нужно,
   потому что модель склонна не замечать собственные типичные ошибки.

Если первый уровень нашёл проблему, второй не вызывается: ответ и так
уйдёт на доработку, а вызов LLM стоит секунды.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from app import telemetry as T
from app.catalog import Catalog, normalize_title
from app.llm import get_llm
from app.memory import Profile
from app.skills import system_prompt
from app.tools import ToolLog

YEAR_RE = re.compile(r"\((\d{4})\)")
# Где заканчивается предыдущий кусок текста перед названием фильма.
SEPARATORS = re.compile(r"(?:\n|^|[:;«»\"*]|\s[-–—]\s|\.\s|,\s|\s(?:и|или|and|or)\s|^\s*\d+[.)]\s*)")
MAX_TITLE_WORDS = 14


@dataclass
class Validation:
    passed: bool
    problems: list[str] = field(default_factory=list)
    mentioned: list[int] = field(default_factory=list)
    stage: str = "rules"  # rules | llm

    def as_dict(self):
        return {"passed": self.passed, "problems": self.problems, "mentioned": self.mentioned, "stage": self.stage}


@dataclass
class Mention:
    text: str  # как написано в ответе
    year: int
    movie_id: int | None  # None: такого фильма в каталоге нет
    year_mismatch: bool = False


def extract_mentions(text: str, catalog: Catalog) -> list[Mention]:
    """Находит в ответе фильмы вида «Название (год)» и сверяет с каталогом.

    Для каждого «(год)» перебираем хвосты текста перед ним, от длинного к
    короткому, пока нормализованное название не совпадёт с каталогом. Так
    находятся и «1. Inception (2010)», и «... и Fight Club (1999)», и
    названия со скобками вроде «Amelie (Fabuleux destin...) (2001)».
    """
    out = []
    for m in YEAR_RE.finditer(text):
        year = int(m.group(1))
        line_start = text.rfind("\n", 0, m.start()) + 1
        before = text[line_start : m.start()]
        words = before.split()[-MAX_TITLE_WORDS:]
        found = None
        for i in range(len(words)):
            norm = normalize_title(" ".join(words[i:]))
            if not norm:
                continue
            if (norm, year) in catalog.by_title_year:
                found = Mention(" ".join(words[i:]).strip("*«»\"' -–—"), year, catalog.by_title_year[(norm, year)])
                break
            if norm in catalog.by_title and found is None:
                found = Mention(" ".join(words[i:]).strip("*«»\"' -–—"), year, catalog.by_title[norm][0], year_mismatch=True)
        if found is None:
            # Не нашли: показываем кусок после последнего разделителя.
            parts = SEPARATORS.split(before)
            candidate = (parts[-1] if parts else before).strip(" *«»\"'-–—")
            if not candidate or YEAR_RE.search(candidate) or len(candidate) < 2:
                continue
            found = Mention(candidate, year, None)
        out.append(found)
    return out


CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def _translated(m: Mention, catalog: Catalog, log: ToolLog) -> int | None:
    """Фильм из инструментов того же года, если название в ответе переведено.

    Каталог английский, а агент отвечает по-русски и может написать
    «Бойцовский клуб (1999)» вместо «Fight Club (1999)». Переводить названия
    обратно мы не умеем, поэтому верим русскому названию, только если такой
    год есть у фильма, который вернули инструменты этого запроса. Латиницу
    так не прощаем: выдуманное английское название это и есть галлюцинация.
    """
    if not CYRILLIC.search(m.text):
        return None
    same_year = [mid for mid in sorted(log.movie_ids) if (catalog.get(mid).year or 0) == m.year]
    return same_year[0] if same_year else None


def check_rules(answer: str, intent: str, catalog: Catalog, profile: Profile, log: ToolLog) -> Validation:
    problems, mentioned = [], []
    for m in extract_mentions(answer, catalog):
        if m.movie_id is None:
            m.movie_id = _translated(m, catalog, log)
        if m.movie_id is None:
            problems.append(f"«{m.text} ({m.year})» нет в каталоге: фильм выдуман")
            T.HALLUCINATIONS.inc()
            continue
        movie = catalog.get(m.movie_id)
        if m.year_mismatch:
            problems.append(f"у «{movie.title}» неверный год: в ответе {m.year}")
        mentioned.append(m.movie_id)
        if m.movie_id not in log.movie_ids:
            problems.append(f"«{movie.title}» не приходил из инструментов, данные о нём не проверены")
    if intent == "recommend":
        if not mentioned and not problems:
            problems.append("в ответе нет ни одного фильма в формате «Название (год)»")
        bad, seen = set(profile.disliked_movies()), set(profile.seen_movies())
        for mid in mentioned:
            if mid in bad:
                problems.append(f"«{catalog.get(mid).title}» пользователь не любит")
            elif mid in seen:
                problems.append(f"«{catalog.get(mid).title}» пользователь уже видел, советовать его не нужно")
    return Validation(not problems, problems, mentioned, "rules")


def _tool_summary(log: ToolLog) -> str:
    lines = []
    for c in log.calls[-6:]:
        lines.append(f"{c['tool']}({json.dumps(c['args'], ensure_ascii=False)}) -> {', '.join(c['result'][:10]) or 'пусто'}")
    return "\n".join(lines) or "Инструменты не вызывались."


def check_llm(question: str, answer: str, profile: Profile, log: ToolLog) -> Validation:
    llm = get_llm("validator", "validator", json_mode=True)
    prompt = (
        f"Запрос пользователя:\n{question}\n\n"
        f"Профиль пользователя:\n{profile.as_text()}\n\n"
        f"Результаты инструментов:\n{_tool_summary(log)}\n\n"
        f"Ответ агента:\n{answer}"
    )
    raw = llm.invoke([SystemMessage(system_prompt("validator")), HumanMessage(prompt)]).content
    try:
        data = json.loads(raw)
        verdict = str(data.get("verdict", "pass")).lower()
        problems = [str(p) for p in data.get("problems", []) if str(p).strip()]
    except (json.JSONDecodeError, AttributeError):
        # Судья ответил не по формату. Блокировать ответ из-за сбоя судьи
        # хуже, чем пропустить: правила по каталогу уже прошли.
        T.VALIDATOR_VERDICTS.labels("judge_error", "llm").inc()
        return Validation(True, [], stage="llm")
    passed = verdict != "fail"
    return Validation(passed, [] if passed else problems or ["судья отклонил ответ без пояснений"], stage="llm")


def validate(question: str, answer: str, intent: str, catalog: Catalog, profile: Profile, log: ToolLog,
             use_llm: bool = True) -> Validation:
    rules = check_rules(answer, intent, catalog, profile, log)
    T.VALIDATOR_VERDICTS.labels("pass" if rules.passed else "fail", "rules").inc()
    if not rules.passed or not use_llm:
        return rules
    judged = check_llm(question, answer, profile, log)
    judged.mentioned = rules.mentioned
    T.VALIDATOR_VERDICTS.labels("pass" if judged.passed else "fail", "llm").inc()
    return judged


def fallback_answer(log: ToolLog, catalog: Catalog, intent: str) -> str:
    """Ответ без LLM из результатов инструментов, если агент не справился.

    Сначала идут фильмы, по которым агент запрашивал подробности: это то,
    о чём спрашивали. Результаты поиска добавляются после них.
    """
    ids = []
    calls = sorted(log.calls, key=lambda c: c["tool"] != "movie_details")
    for c in calls:
        for title in c["result"]:
            m = catalog.find_by_title(title)
            if m and m.movie_id not in ids:
                ids.append(m.movie_id)
    if not ids:
        return "Не получилось найти подходящие фильмы в каталоге. Попробуйте переформулировать запрос."
    head = "Вот что нашлось в каталоге:" if intent == "movie_info" else "Вот что могу предложить по каталогу:"
    lines = []
    for mid in ids[: 3 if intent == "movie_info" else 5]:
        d = catalog.details(mid)
        lines.append(f"{len(lines) + 1}. {d['title']}: {', '.join(d['genres'])}, средняя оценка {d['avg_rating']} из 5 "
                     f"по {d['num_ratings']} оценкам")
    return head + "\n" + "\n".join(lines)


__all__ = ["Mention", "Validation", "validate", "fallback_answer", "extract_mentions", "check_rules"]
