from app.catalog import normalize_title


def test_normalize_title_handles_articles_and_markdown():
    assert normalize_title("Matrix, The (1999)") == normalize_title("**The Matrix**") == "matrix"
    assert normalize_title("Amelie (Fabuleux destin d'Amélie Poulain, Le) (2001)") == "amelie"


def test_search_exact_typo_and_missing(catalog):
    assert catalog.find_by_title("Inception").title == "Inception (2010)"
    assert catalog.find_by_title("Interstelar").title == "Interstellar (2014)"
    assert catalog.find_by_title("Песни") is None
    assert catalog.search("qwertyuiop") == []


def test_recommend_excludes_liked_and_respects_filters(catalog):
    inception = catalog.find_by_title("Inception").movie_id
    recs = catalog.recommend([inception], genre="Comedy", year_from=2000, k=5)
    assert len(recs) == 5
    assert inception not in [m.movie_id for m in recs]
    assert all("Comedy" in m.genres and m.year >= 2000 for m in recs)


def test_cold_start_returns_top_rated(catalog):
    recs = catalog.recommend([], k=5)
    assert len(recs) == 5
    assert all(catalog.details(m.movie_id)["num_ratings"] >= 10 for m in recs)


def test_details(catalog):
    d = catalog.details(catalog.find_by_title("Matrix").movie_id)
    assert d["year"] == 1999 and "Sci-Fi" in d["genres"] and 0 < d["avg_rating"] <= 5
    assert catalog.details(10**9) is None
