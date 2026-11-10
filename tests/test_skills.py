from app.skills import load_skills, skill_for_agent, system_prompt


def test_all_skills_parse_and_have_known_tools():
    known = {"search_movies", "movie_details", "similar_movies", "recommend_for_user", "top_movies"}
    skills = load_skills()
    assert {"recommend-movies", "movie-facts", "remember-taste", "validate-answer"} <= set(skills)
    for s in skills.values():
        assert s.description and s.body.strip()
        assert set(s.tools) <= known


def test_references_are_attached():
    assert "Sci-Fi (фантастика" in skill_for_agent("recommender").full_text()


def test_prompt_composition():
    p = system_prompt("recommender", "Нравится: Inception (2010)")
    assert "# Кто я" in p and "recommend-movies" in p and "Inception" in p
    # Служебным агентам SOUL.md не нужен.
    assert "# Кто я" not in system_prompt("validator")
