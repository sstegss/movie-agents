from app.memory import Fact, Profile
from app.tools import ToolLog
from app.validator import check_rules, extract_mentions, fallback_answer


def _log(catalog, *titles):
    log = ToolLog()
    log.record("search_movies", {}, [catalog.find_by_title(t) for t in titles])
    return log


def test_extract_mentions_variants(catalog):
    text = "1. **The Matrix** (1999): классика\n2. Советую «Interstellar» (2014) и Fight Club (1999).\nОценка 4.2 (из 5)"
    found = [(m.movie_id, m.year_mismatch) for m in extract_mentions(text, catalog)]
    ids = [catalog.find_by_title(t).movie_id for t in ("Matrix", "Interstellar", "Fight Club")]
    assert found == [(i, False) for i in ids]


def test_hallucinated_movie_fails(catalog):
    v = check_rules("1. Песни (2015): шедевр Нолана", "recommend", catalog, Profile([], []), ToolLog())
    assert not v.passed and "выдуман" in v.problems[0]


def test_wrong_year_fails(catalog):
    log = _log(catalog, "Dark Knight")
    v = check_rules("The Dark Knight (2009)", "recommend", catalog, Profile([], []), log)
    assert not v.passed and "неверный год" in v.problems[0]


def test_movie_not_from_tools_fails(catalog):
    v = check_rules("Fight Club (1999)", "recommend", catalog, Profile([], []), _log(catalog, "Matrix"))
    assert not v.passed and "не приходил из инструментов" in v.problems[0]


def test_disliked_movie_fails(catalog):
    tid = catalog.find_by_title("Titanic").movie_id
    profile = Profile([Fact(f"movie:{tid}", "movie", "Titanic", "dislike", tid)], [])
    title = catalog.get(tid).title
    v = check_rules(f"{title}: попробуйте", "recommend", catalog, profile, _log(catalog, "Titanic"))
    assert not v.passed and "не любит" in " ".join(v.problems)


def test_grounded_answer_passes(catalog):
    log = _log(catalog, "Matrix", "Inception")
    v = check_rules("1. Matrix, The (1999)\n2. Inception (2010)", "recommend", catalog, Profile([], []), log)
    assert v.passed and len(v.mentioned) == 2


def test_recommendation_without_movies_fails(catalog):
    v = check_rules("Посмотрите что-нибудь хорошее!", "recommend", catalog, Profile([], []), ToolLog())
    assert not v.passed


def test_fallback_uses_only_tool_results(catalog):
    text = fallback_answer(_log(catalog, "Matrix"), catalog, "recommend")
    assert "Matrix, The (1999)" in text
    assert "Не получилось" in fallback_answer(ToolLog(), catalog, "recommend")


def test_already_seen_movie_fails(catalog):
    mid = catalog.find_by_title("Fight Club").movie_id
    profile = Profile([Fact(f"movie:{mid}", "movie", "Fight Club (1999)", "like", mid)], [])
    v = check_rules("1. Fight Club (1999)", "recommend", catalog, profile, _log(catalog, "Fight Club"))
    assert not v.passed and "уже видел" in v.problems[0]


def test_russian_title_of_tool_movie_passes(catalog):
    log = _log(catalog, "Fight Club")
    v = check_rules("Бойцовский клуб (1999) вышел в 1999 году.", "movie_info", catalog, Profile([], []), log)
    assert v.passed and v.mentioned == [catalog.find_by_title("Fight Club").movie_id]


def test_russian_title_without_tool_movie_fails(catalog):
    v = check_rules("Песни (2015)", "recommend", catalog, Profile([], []), _log(catalog, "Fight Club"))
    assert not v.passed


def test_fallback_prefers_details(catalog):
    log = ToolLog()
    log.record("search_movies", {}, catalog.search("Matrix", limit=3))
    log.record("movie_details", {}, [catalog.find_by_title("Matrix Reloaded")])
    text = fallback_answer(log, catalog, "movie_info")
    assert text.splitlines()[1].startswith("1. Matrix Reloaded")
