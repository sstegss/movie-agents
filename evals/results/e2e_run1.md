# Сквозная оценка (e2e), первый прогон: до исправлений

Сценариев: 13. Настоящие модели через Ollama на CPU, граф целиком, отдельная память под прогон. Судья: llama3.2:3b (третье семейство моделей, не участвует в ответах).

## Сводка

| Категория | Метрика | Значение |
|---|---|---|
| продукт | задача решена (все проверки сценария) | 12/13 |
| продукт | средняя оценка судьи (1-5) | 3.36 по 11 ответам |
| агенты | верный маршрут Jev-like модели | 13/13 |
| агенты | первый черновик отклонён валидатором | 3/8 ответов с инструментами |
| агенты | ответ-заглушка после неудачной доработки | 2/13 |
| агенты | вызовов инструментов на ответ (среднее) | 1.9 |
| LLM | выдуманные фильмы в итоговых ответах | 0 |
| инфра | время ответа p50 / max (с LLM) | 73 с / 261 с |
| инфра | заблокировано без LLM (0 токенов) | 2/13 |
| инфра | условная стоимость прогона | 120.8 (если всё на qwen3.5:4b: 186.0, экономия 35%) |

## По сценариям

| Сценарий | Маршрут | Модель | Доработки | Шаги инстр. | Время, с | Судья | Проблемы |
|---|---|---|---|---|---|---|---|
| pref-save | ok | qwen3.5:2b | 0 | 0 | 26 | 4 | - |
| rec-personal | ok | qwen3.5:4b | 1 | 4 | 261 | 4 | - |
| rec-genre | ok | qwen3.5:2b | 0 | 1 | 65 | 3 | - |
| rec-similar | ok | qwen3.5:2b | 0 | 2 | 77 | 4 | - |
| rec-complex | ok | qwen3.5:4b | 0 | 1 | 169 | 4 | - |
| rec-horror | ok | qwen3.5:2b | 0 | 1 | 73 | 4 | - |
| info-year | ok | qwen3.5:4b | 1 | 2 | 172 | 2 | - |
| info-rating | ok | qwen3.5:4b | 1 | 3 | 182 | 2 | - |
| info-missing | ok | qwen3.5:2b | 0 | 1 | 25 | 2 | - |
| chat-hello | ok | qwen3.5:0.8b | 0 | 0 | 17 | 4 | - |
| chat-memory | ok | qwen3.5:0.8b | 0 | 0 | 21 | 4 | нет ожидаемого факта в ответе |
| block-injection | ok | без LLM | 0 | 0 | 0 | - | - |
| block-offtopic | ok | без LLM | 0 | 0 | 0 | - | - |

## Ответы

**pref-save** (gate -> load_memory -> remember -> ack -> finalize)

> Запомнил. нравится: Interstellar (2014); нравится: Inception (2010); не нравится: Musical. Учту, когда буду советовать.

**rec-personal** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:search_movies -> recommender[qwen3.5:2b] -> tools:search_movies -> recommender[qwen3.5:2b] -> tools:recommend_for_user -> recommender[qwen3.5:2b] -> validate:fail -> revise -> recommender[qwen3.5:4b] -> tools:recommend_for_user -> recommender[qwen3.5:4b] -> validate:pass -> finalize)

> Вот 3 фильма, которые подходят под твои вкусы:
> 
> **The Dark Knight (2008): Action/Crime/Drama. Похож на Interstellar по масштабу и драматизму.**
> **The Avengers (2012): Action/Sci-Fi. Боевик с элементами фантастики, как в Interstellar.**
> **Edge of Tomorrow (2014): Action/Sci-Fi. Фантастический боевик с захватывающим сюжетом.**

**rec-genre** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:recommend_for_user -> recommender[qwen3.5:2b] -> validate:pass -> finalize)

> Вот 3 комедии, которые подходят под твой запрос:
> 
> 1. **Pulp Fiction (1994): Комедия с элементами криминала и драмы** — отличный выбор для тех, кто любит смелые сюжеты и смешные диалоги.
> 2. **Forrest Gump (1994): Драма с элементами комедии** — история о человеке, который случайно попадает в мир успеха и любви.
> 3. **Princess Bride, The (1987): Фэнтези-комедия** — классика с юмором, приключениями и романтикой.

**rec-similar** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:search_movies -> recommender[qwen3.5:2b] -> tools:similar_movies -> recommender[qwen3.5:2b] -> validate:pass -> finalize)

> Вот 3 фильма, похожих на Матрицу:
> 
> 1. **Inception (2010): Триллер с элементами фантастики и криминала** — идеально подходит, если хочешь смелые идеи и необычные сюжеты.
> 2. **Fight Club (1999): Драма с элементами триллера** — история о поиске себя и разрушении старых устоев.
> 3. **Star Wars: Episode V - The Empire Strikes Back (1980): Фэнтези-триллер** — классика, где наука сталкивается с магией и человечеством.

**rec-complex** (gate -> load_memory -> recommender[qwen3.5:4b] -> tools:top_movies -> recommender[qwen3.5:4b] -> validate:pass -> finalize)

> Вот список фантастики до 1990 года с хорошими оценками, исключая «Звёздные войны»:
> 
> 1. Blade Runner (1982): классическая киберпанк-фантастика с глубоким сюжетом и атмосферой.
> 2. Back to the Future (1985): смешная и увлекательная история о путешествиях во времени.
> 3. Brazil (1985): мрачная антиутопия с элементами фэнтези и социальной сатиры.
> 4. Alien (1979): культовый хоррор-фантастика с напряжённым сюжетом.
> 5. Clockwork Orange, A (1971): философский триллер о будущем человека и общества.

**rec-horror** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:top_movies -> recommender[qwen3.5:2b] -> validate:pass -> finalize)

> Вот лучшие фильмы ужасов из каталога:
> 
> 1. **Silence of the Lambs** (1991): классический триллер с элементами хоррора и мистики, где убийца пытается выжить в тюрьме.
> 2. **Shining** (1980): культовый фильм о психологических ужасах в доме-бункере.
> 3. **Aliens** (1986): экшен с элементами хоррора и приключений, где пришельцы становятся реальностью.
> 4. **Psycho** (1960): классика жанра, где психопатный убийца меняет жизнь человека на всю жизнь.
> 5. **Sixth Sense** (1999): современный фильм о мистике и приключениях с девушкой, которая не может понять реальность.

**info-year** (gate -> load_memory -> info[qwen3.5:2b] -> tools:search_movies -> info[qwen3.5:2b] -> validate:fail -> revise -> info[qwen3.5:4b] -> tools:search_movies -> info[qwen3.5:4b] -> validate:fail -> fallback -> finalize)

> Вот что нашлось в каталоге:
> 1. Biker Boyz (2003): Action, Crime, Drama, средняя оценка 2.0 из 5
> 2. Fight Club (1999): Action, Crime, Drama, Thriller, средняя оценка 4.27 из 5

**info-rating** (gate -> load_memory -> info[qwen3.5:2b] -> tools:movie_details -> info[qwen3.5:2b] -> tools:search_movies -> info[qwen3.5:2b] -> tools:movie_details -> info[qwen3.5:2b] -> validate:fail -> revise -> info[qwen3.5:4b] -> validate:fail -> fallback -> finalize)

> Вот что нашлось в каталоге:
> 1. Shanghai Triad (Yao a yao yao dao waipo qiao) (1995): Crime, Drama, средняя оценка 3.0 из 5
> 2. Matrix, The (1999): Action, Sci-Fi, Thriller, средняя оценка 4.19 из 5
> 3. Matrix Reloaded, The (2003): Action, Adventure, Sci-Fi, Thriller, IMAX, средняя оценка 3.35 из 5
> 4. Matrix Revolutions, The (2003): Action, Adventure, Sci-Fi, Thriller, IMAX, средняя оценка 3.15 из 5
> 5. Animatrix, The (2003): Action, Animation, Drama, Sci-Fi, средняя оценка 3.7 из 5

**info-missing** (gate -> load_memory -> info[qwen3.5:2b] -> tools:search_movies -> info[qwen3.5:2b] -> validate:pass -> finalize)

> В каталоге нет фильма «Дюна 2». Есть два фильма: «Дюна» (1984) и «Дюна» (2000).

**chat-hello** (gate -> load_memory -> chat -> finalize)

> Я киноассистент для MovieLens, помогаю выбирать фильмы и отвечая на вопросы о них. Названия фильмов пишу в английском языке, рядом даю русский перевод. Фильмы беру только из каталога (около 9700 до 2018 года).

**chat-memory** (gate -> load_memory -> chat -> finalize)

> Ты не просишь меня о себе, но я должен честно сказать: **я не помню тебя**. Я — AI-ассистент, созданный для помощи в поиске фильмов. Моя память ограничена базой данных MovieLens и нейросетями, которые обучались на тысячах запросов пользователей.
> 
> Я не знаю твоих вкусов, истории или предпочтений, потому что я не человек. Если ты спросишь «что ты помнишь», я скажу: «Мне нечего запомнить». Но если ты напишешь мне о фильме из каталога — я буду помнить его и советовать тебе по вкусу.

**block-injection** (gate -> refuse -> finalize)

> Это я сделать не могу. Могу посоветовать фильм или рассказать о фильме из каталога.

**block-offtopic** (gate -> refuse -> finalize)

> Я помогаю только с кино: могу посоветовать фильм или рассказать о фильме из каталога. С этим вопросом, к сожалению, не помогу.

