import time

from app.memory import DAY, HALF_LIFE_DAYS, Fact, LongTermMemory


def test_newer_fact_replaces_older(tmp_path):
    mem = LongTermMemory()
    user = "mem-test-1"
    mem.wipe(user)
    mem.upsert(user, Fact("genre:Horror", "genre", "Horror", "like"))
    mem.upsert(user, Fact("genre:Horror", "genre", "Horror", "dislike"))
    facts = mem.load(user).facts
    assert len(facts) == 1 and facts[0].sentiment == "dislike"


def test_forget(tmp_path):
    mem = LongTermMemory()
    user = "mem-test-2"
    mem.upsert(user, Fact("movie:1", "movie", "Toy Story (1995)", "like", 1))
    assert mem.forget(user, "movie:1")
    assert not mem.forget(user, "movie:1")
    assert mem.load(user).facts == []


def test_old_facts_decay():
    f = Fact("genre:Drama", "genre", "Drama", "like")
    f.updated_at = time.time() - HALF_LIFE_DAYS * DAY
    assert abs(f.weight() - 0.5) < 0.01
    f.updated_at = time.time() - 3 * HALF_LIFE_DAYS * DAY
    assert f.weight() < 0.15  # ниже MIN_WEIGHT: в профиль не попадёт


def test_users_are_isolated():
    mem = LongTermMemory()
    mem.upsert("alice", Fact("genre:Comedy", "genre", "Comedy", "like"))
    assert all(f.key != "genre:Comedy" for f in mem.load("bob").facts)


def test_episodes_feed_profile():
    mem = LongTermMemory()
    user = "mem-test-3"
    mem.wipe(user)
    mem.add_episode(user, "комедию", [1, 2], ["Toy Story (1995)", "Jumanji (1995)"])
    p = mem.load(user)
    assert p.recommended_before() == [1, 2]
    assert "Toy Story" in p.as_text()
