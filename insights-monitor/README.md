# Telegram Content Insights

Отдельный сервис для поиска повторяющихся тем и проблем в выбранных Telegram-сообществах и сбора редакционных заметок в закрытом канале.

Для релевантного сообщения сервис отправляет:

- тему и краткое описание обозначенной проблемы;
- контекст и потребность, если они понятны из сообщения;
- 1–3 идеи для статей или постов;
- название источника, дату и ссылку на оригинал, если Telegram предоставляет её.

Имя автора и Telegram ID не сохраняются. Текст исходной публикации сохраняется в отдельной SQLite-базе только для релевантных сообщений. Нерелевантные сообщения отмечаются обработанными без сохранения текста.

## Источники и получатель

`config/sources.yaml` содержит четыре источника:

- `@aa_online_chat`
- `@doctorkisler`
- `@trezvyiy_dom`
- `@livestoriesTD`

Материалы отправляются в `Материалы`, ID `-1004421917588`.

Аккаунт сервиса должен состоять в источниках и иметь право отправлять сообщения в «Материалы». Файл сессии отдельный от музыкального монитора.

## Установка на VPS

Сервис находится в отдельной папке `/opt/telegram-commenter/insights-monitor`. Он запускается отдельным Docker Compose проектом и использует собственные контейнер, конфигурацию, SQLite-базу и Telegram-сессию.

### 1. Получить проект на сервере

Код входит в текущий репозиторий `telegram-commenter` и будет доступен в `/opt/telegram-commenter/insights-monitor` после обновления:

~~~bash
cd /opt/telegram-commenter
git pull
cd insights-monitor
~~~

Не копируйте в Git `.env` или файл сессии.

### 2. Заполнить `.env`

~~~bash
cd /opt/telegram-commenter/insights-monitor
cp .env.example .env
nano .env
~~~

Укажите `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` от своего Telegram-приложения. В `GIGACHAT_AUTH_KEY` вставьте ключ авторизации из проекта GigaChat API. Другие начальные параметры:

~~~env
TELEGRAM_SESSION=/data/insights.session
DATABASE_PATH=/data/insights.db
SOURCES_CONFIG=/app/config/sources.yaml
DRY_RUN=true
MONITOR_LOOKBACK_HOURS=24
MONITOR_POLL_SECONDS=300
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-2
GIGACHAT_API_BASE=https://api.giga.chat
~~~

Приложение само получает и обновляет короткоживущий access token GigaChat. Для нового подключения используется адрес `https://api.giga.chat` согласно [официальной документации GigaChat](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api).

### 3. Создать отдельную Telegram-сессию

Войди под аккаунтом, который состоит во всех источниках и может писать в «Материалы»:

~~~bash
mkdir -p data
sudo chown -R 10001:10001 data
docker compose build
docker compose run --rm insights python -m app.init_session
~~~

Введи номер, код из Telegram и пароль 2FA, если его запросит система. Файл `data/insights.session` — секрет уровня пароля.

Проверь аккаунт и доступные чаты:

~~~bash
docker compose run --rm insights python -m app.list_dialogs
~~~

Проверь первую строку с аккаунтом, затем наличие «Материалы» и источников.

### 4. Проверить безопасный режим

Источники и получатель уже прописаны в `config/sources.yaml`. Если username какого-либо закрытого источника не разрешается, замени его на числовой ID из `app.list_dialogs`.

~~~bash
docker compose up -d --build
docker compose logs -f --tail=150 insights
~~~

В логах ожидаются четыре записи `source_registered`. Для найденного материала появится `insight_dry_run` с темой, краткой болью и ссылкой; при `DRY_RUN=true` ничего не отправляется. Выйти из просмотра логов можно через `Ctrl+C`, контейнер продолжит работать.

### 5. Включить отправку материалов

В `.env` поменяй:

~~~env
DRY_RUN=false
~~~

Перезапусти:

~~~bash
docker compose up -d --force-recreate
docker compose logs -f --tail=150 insights
~~~

Релевантные сообщения будут анализироваться GigaChat и отправляться в «Материалы». Сохранённые во время dry-run заметки тоже будут опубликованы после переключения на `false`. При первом запуске сервис смотрит назад на 24 часа, затем продолжает с сохранённых курсоров. Одно сообщение обрабатывается только один раз.

## Управление

Состояние и последние логи:

~~~bash
cd /opt/telegram-commenter/insights-monitor
docker compose ps
docker compose logs --tail=150 insights
~~~

Перезапуск после изменений `.env` или `config/sources.yaml`:

~~~bash
docker compose up -d --force-recreate
~~~

В `config/sources.yaml` можно изменить `min_message_length` (пропускать короткие сообщения) и `confidence_threshold` (минимальная уверенность, с которой заметка отправляется в канал).

В SQLite остаются исходные тексты только релевантных сообщений, заметки и ссылки. Не удаляй `data/insights.db` при диагностике: в ней хранятся также отметки о прочитанных сообщениях.

## Основные события в логах

- `source_registered` — источник подключён;
- `insight_detected` — релевантная тема сохранена;
- `insight_dry_run` — сообщение было бы отправлено, но включён безопасный режим;
- `insight_published` — заметка отправлена;
- `catch_up_failed`, `message_analysis_failed`, `insight_publish_failed` — ошибка обхода, анализа или публикации.
