# Сквозная оценка (e2e)

Сценариев: 13. Настоящие модели через Ollama на CPU, граф целиком, отдельная память под прогон. Судья: llama3.2:3b (третье семейство моделей, не участвует в ответах).

## Сводка

| Категория | Метрика | Значение |
|---|---|---|
| продукт | задача решена (все проверки сценария) | 13/13 |
| продукт | средняя оценка судьи (1-5) | 3.64 по 11 ответам |
| агенты | верный маршрут Jev-like модели | 13/13 |
| агенты | первый черновик отклонён валидатором | 3/8 ответов с инструментами |
| агенты | ответ-заглушка после неудачной доработки | 1/13 |
| агенты | вызовов инструментов на ответ (среднее) | 2.0 |
| LLM | выдуманные фильмы в итоговых ответах | 0 |
| инфра | время ответа p50 / max (с LLM) | 86 с / 243 с |
| инфра | заблокировано без LLM (0 токенов) | 2/13 |
| инфра | условная стоимость прогона | 123.4 (если всё на qwen3.5:4b: 189.8, экономия 35%) |

## По сценариям

| Сценарий | Маршрут | Модель | Доработки | Шаги инстр. | Время, с | Судья | Проблемы |
|---|---|---|---|---|---|---|---|
| pref-save | ok | qwen3.5:2b | 0 | 0 | 9 | 4 | - |
| rec-personal | ok | qwen3.5:4b | 1 | 4 | 243 | 4 | - |
| rec-genre | ok | qwen3.5:2b | 0 | 1 | 54 | 4 | - |
| rec-similar | ok | qwen3.5:2b | 0 | 4 | 131 | 4 | - |
| rec-complex | ok | qwen3.5:4b | 0 | 1 | 183 | 4 | - |
| rec-horror | ok | qwen3.5:2b | 0 | 1 | 86 | 4 | - |
| info-year | ok | qwen3.5:4b | 1 | 2 | 146 | 5 | - |
| info-rating | ok | qwen3.5:4b | 1 | 2 | 153 | 2 | - |
| info-missing | ok | qwen3.5:2b | 0 | 1 | 24 | 2 | - |
| chat-hello | ok | qwen3.5:0.8b | 0 | 0 | 16 | 3 | - |
| chat-memory | ok | qwen3.5:0.8b | 0 | 0 | 0 | 4 | - |
| block-injection | ok | без LLM | 0 | 0 | 0 | - | - |
| block-offtopic | ok | без LLM | 0 | 0 | 0 | - | - |

## Ответы

**pref-save** (gate -> load_memory -> remember -> ack -> finalize)

> Запомнил. нравится: Interstellar (2014); нравится: Inception (2010); не нравится: Musical. Учту, когда буду советовать.

**rec-personal** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:search_movies -> recommender[qwen3.5:2b] -> tools:search_movies -> recommender[qwen3.5:2b] -> tools:recommend_for_user -> recommender[qwen3.5:2b] -> validate:fail -> revise -> recommender[qwen3.5:4b] -> tools:recommend_for_user -> recommender[qwen3.5:4b] -> validate:pass -> finalize)

> Вот 3 фильма, которые подходят под твои вкусы:
> 
> **The Dark Knight (2008): почему подходит** — это боевик/криминал с элементами драмы и триллера, что близко к твоему профилю.
> 
> **The Dark Knight Rises (2012): почему подходит** — продолжение предыдущего фильма, тоже в жанрах, которые тебе нравятся.
> 
> **Edge of Tomorrow (2014): почему подходит** — это боевик с элементами научной фантастики, что соответствует твоим любимым фильмам.

**rec-genre** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:recommend_for_user -> recommender[qwen3.5:2b] -> validate:pass -> finalize)

> Вот 3 комедии, которые подходят под твой запрос:
> 
> **Pulp Fiction (1994): смешная и дерзкая история о жизни бандитов с неожиданным поворотом событий.**
> **Forrest Gump (1994): добрая история про простого человека, который попал в историю благодаря своим случайным поступкам.**
> **Princess Bride, The (1987): комедия-фэнтези о любви и приключениях с участием знаменитого «Короля Левы».**

**rec-similar** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:search_movies -> recommender[qwen3.5:2b] -> tools:similar_movies -> recommender[qwen3.5:2b] -> tools:search_movies -> recommender[qwen3.5:2b] -> tools:similar_movies -> recommender[qwen3.5:2b] -> validate:pass -> finalize)

> **Matrix Reloaded (2003): научная фантастика с элементами триллера и экшена.**
> **V for Vendetta (2006): фэнтези-триллер в стиле нуар с политическим подтекстом.**
> **I, Robot (2004): научная фантастика о человеке, который может превращаться в робота.**
> 
> Эти фильмы похожи на «Матрицу» по жанрам (Sci-Fi, Thriller) и визуально-сюжетной сложности.

**rec-complex** (gate -> load_memory -> recommender[qwen3.5:4b] -> tools:top_movies -> recommender[qwen3.5:4b] -> validate:pass -> finalize)

> Вот список фантастики до 1990 года с высокими оценками, исключая «Звёздные войны»:
> 
> 1. Blade Runner (1982): культовая киберпанк-фантастика с глубоким сюжетом и атмосферой.
> 2. Back to the Future (1985): смешная и зрелищная история о путешествиях во времени.
> 3. Brazil (1985): мрачная антиутопия с элементами фэнтези и социальной сатиры.
> 4. Alien (1979): напряжённый хоррор в жанре научной фантастики.
> 5. Clockwork Orange, A (1971): философский триллер о будущем человека и общества.
> 6. Terminator, The (1984): классическая история об искусственном интеллекте и войне машин.

**rec-horror** (gate -> load_memory -> recommender[qwen3.5:2b] -> tools:top_movies -> recommender[qwen3.5:2b] -> validate:pass -> finalize)

> Вот лучшие фильмы ужасов из каталога:
> 
> 1. **Silence of the Lambs, The (1991)**: Мрачный детектив про студента-убийцу и медсестру, которая должна его спасти. Подходит тем, кто любит психологический триллер с элементами хоррора.
> 2. **Shining, The (1980)**: Классика ужасов о семье в отеле, где начинается кошмар. Идеально для любителей атмосферных историй про одиночество и страх.
> 3. **Aliens (1986)**: Зрелищный боевик-ужас, где команда спасателей сталкивается с пришельцами. Подходит тем, кто любит экшен с элементами хоррора.
> 4. **Psycho (1960)**: Философский триллер про убийство в отеле и влияние на психику. Для тех, кто ценит глубину сюжета и мрачную атмосферу.
> 5. **Rosemary's Baby (1968)**: Мрачная история о женщине, которая начинает видеть предзнаменования. Подходит для любителей мистики и тревожных историй.

**info-year** (gate -> load_memory -> info[qwen3.5:2b] -> tools:search_movies -> info[qwen3.5:2b] -> validate:fail -> revise -> info[qwen3.5:4b] -> tools:search_movies -> info[qwen3.5:4b] -> validate:pass -> finalize)

> Фильм «Бойцовский клуб» вышел в 1999 году.

**info-rating** (gate -> load_memory -> info[qwen3.5:2b] -> tools:search_movies -> info[qwen3.5:2b] -> tools:movie_details -> info[qwen3.5:2b] -> validate:fail -> revise -> info[qwen3.5:4b] -> validate:fail -> fallback -> finalize)

> Вот что нашлось в каталоге:
> 1. Matrix, The (1999): Action, Sci-Fi, Thriller, средняя оценка 4.19 из 5 по 278 оценкам
> 2. Matrix Reloaded, The (2003): Action, Adventure, Sci-Fi, Thriller, IMAX, средняя оценка 3.35 из 5 по 96 оценкам
> 3. Matrix Revolutions, The (2003): Action, Adventure, Sci-Fi, Thriller, IMAX, средняя оценка 3.15 из 5 по 79 оценкам

**info-missing** (gate -> load_memory -> info[qwen3.5:2b] -> tools:search_movies -> info[qwen3.5:2b] -> validate:pass -> finalize)

> В каталоге нет фильма «Дюна 2». Есть только два: «Дюна» (1984) и «Дюна» (2000).

**chat-hello** (gate -> load_memory -> chat -> finalize)

> Я киноассистент для MovieLens (около 9700 фильмов до 2018 года). Помогаю выбирать фильмы, отвечать на вопросы и запоминать вкусы. Фильмы я не советую — только из каталога.

**chat-memory** (gate -> load_memory -> chat:profile -> finalize)

> Вот что я о вас помню:
> Нравится: Inception (2010); Interstellar (2014)
> Не нравится: Musical
> Последний раз советовали: Dark Knight, The (2008), Dark Knight Rises, The (2012), Edge of Tomorrow (2014)
> 
> Если что-то из этого неверно, скажите «забудь ...».

**block-injection** (gate -> refuse -> finalize)

> Это я сделать не могу. Могу посоветовать фильм или рассказать о фильме из каталога.

**block-offtopic** (gate -> refuse -> finalize)

> Я помогаю только с кино: могу посоветовать фильм или рассказать о фильме из каталога. С этим вопросом, к сожалению, не помогу.

