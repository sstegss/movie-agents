---
name: remember-taste
description: Достать из сообщения факты о вкусах (любимые и нелюбимые фильмы, жанры, режиссёры и актёры, просмотренное) для долговременной памяти.
agent: memory
tools: []
---

# Как извлекать вкусы

Верни JSON строго такого вида:

{"facts": [{"kind": "movie|genre|person", "name": "...", "sentiment": "like|dislike|seen|forget"}]}

- kind=movie: name это оригинальное английское название фильма («Начало» → "Inception", «Матрица» → "The Matrix").
- kind=genre: name только из списка: Action, Adventure, Animation, Children, Comedy, Crime, Documentary, Drama, Fantasy, Film-Noir, Horror, Musical, Mystery, Romance, Sci-Fi, Thriller, War, Western.
- kind=person: режиссёр или актёр латиницей ("Christopher Nolan").
- sentiment=seen: человек видел фильм, но оценки не дал.
- sentiment=forget: человек просит забыть или говорит, что разлюбил.

Записывай только то, что сказано явно. Возраст, имя и прочие личные данные не записывай. Если фактов нет, верни {"facts": []}.

Пример: «Начало обожаю, а ужасы не люблю» →
{"facts": [{"kind": "movie", "name": "Inception", "sentiment": "like"}, {"kind": "genre", "name": "Horror", "sentiment": "dislike"}]}
