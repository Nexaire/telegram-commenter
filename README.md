# Telegram-монитор заявок на кавер-группу

Сервис авторизуется в Telegram как пользователь через Telethon, мониторит заданные
каналы и чаты и ищет сообщения, в которых упоминается требуемая кавер-группа.
Найденное объявление вместе со ссылкой на оригинал публикуется в специальный
Telegram-канал. Комментарии сервис не создаёт и не публикует; LLM не используется.

## Как работает

1. Новые сообщения поступают через Telegram events.
2. Периодический проход восстанавливает сообщения, пропущенные при перезапуске.
3. Текст нормализуется: регистр, ё/е, пробелы и разные виды тире.
4. Ищется ключевая фраза: кавер-группа, кавер-группу, cover band и т. п.
5. В строгом режиме также требуется признак заявки: ищем, нужна, требуется,
   на свадьбу, на корпоратив и т. п.
6. Самопрезентации вроде «мы кавер-группа» отбрасываются.
7. SQLite не позволяет обработать одно сообщение повторно.
8. В целевой канал отправляется текст, название источника и ссылка на оригинал.
9. Неотправленные записи остаются в очереди и повторяются с увеличивающейся паузой.

## Ограничения Telegram

- Пользовательский аккаунт должен состоять во всех приватных источниках.
- Он должен иметь право публиковать сообщения в целевой канал.
- Публичные сообщения получают ссылку вида https://t.me/username/message_id.
- Для приватной супергруппы или канала создаётся ссылка https://t.me/c/...;
  открыть её смогут только участники источника.
- У обычной старой приватной группы Telegram нет стабильной ссылки на сообщение.
  Текст будет опубликован, но вместо ссылки появится предупреждение.

## 1. Подготовьте .env

~~~bash
cp .env.example .env
~~~

Заполните:

~~~env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=ваш_api_hash
TELEGRAM_SESSION=/data/monitor.session

DATABASE_PATH=/data/monitor.db
CHANNELS_CONFIG=/app/config/channels.yaml
LOG_LEVEL=INFO
DRY_RUN=true
MONITOR_LOOKBACK_HOURS=24
MONITOR_POLL_SECONDS=300
~~~

TELEGRAM_API_ID и TELEGRAM_API_HASH получают на
[my.telegram.org](https://my.telegram.org/) → API development tools.

## 2. Настройте источники и целевой канал

Отредактируйте config/channels.yaml:

~~~yaml
target_channel: my_cover_leads

matching:
  keywords:
    - "кавер-групп"
    - "кавер групп"
    - "кавер-бэнд"
    - "кавер бэнд"
    - "cover band"

  require_intent: false
  intent_phrases:
    - "ищем"
    - "ищу"
    - "нужна"
    - "нужны"
    - "требуется"
    - "посоветуйте"
    - "на свадьбу"
    - "на корпоратив"
    - "на мероприятие"

  exclude_phrases:
    - "мы кавер-группа"
    - "мы кавер группа"
    - "наша кавер-группа"
    - "наша кавер группа"

sources:
  - entity: wedding_moscow_chat
    enabled: true
  - entity: event_jobs
    enabled: true
~~~

Username указывается без @ и без https://t.me/. Для доступного приватного
источника можно указать числовой Telegram entity ID.

По умолчанию пересылается любое сообщение с ключевой фразой, кроме явно
исключённых самопрезентаций. Если лишних совпадений много, установите
require_intent: true — тогда потребуется ещё одна фраза из intent_phrases.

## 3. Создайте Telethon session

~~~bash
mkdir -p data
docker compose build
docker compose run --rm commenter python -m app.init_session
~~~

Введите номер пользовательского аккаунта, код из Telegram и пароль 2FA. Сессия
появится в data/monitor.session. Это секрет уровня пароля.

Если контейнер не может записывать в /data на Linux:

~~~bash
sudo chown -R 10001:10001 data
~~~

## 4. Безопасная проверка

Оставьте DRY_RUN=true и запустите:

~~~bash
docker compose up -d --build
docker compose logs -f --tail=200 commenter
~~~

Отправьте в один из тестовых чатов:

~~~text
Ищем кавер-группу на корпоратив 20 декабря. Москва.
~~~

В логах должно появиться событие match_dry_run. В целевой канал при этом ничего
не отправляется. Совпадение сохраняется в data/monitor.db.

Сообщение «Мы кавер-группа из Москвы, выступаем на корпоративах» должно быть
отброшено как самопрезентация.

## 5. Включите публикацию

Установите в .env:

~~~env
DRY_RUN=false
~~~

Примените:

~~~bash
docker compose up -d --force-recreate
docker compose logs -f --tail=200 commenter
~~~

Теперь новые совпадения публикуются в target_channel.

## Обновление существующей установки

~~~bash
cd /opt/telegram-commenter
git pull
docker compose up -d --build --force-recreate
docker compose logs -f --tail=200 commenter
~~~

Старый файл commenter.session можно переименовать:

~~~bash
mv data/commenter.session data/monitor.session
~~~

Либо оставьте TELEGRAM_SESSION=/data/commenter.session в .env.

Старая SQLite-база совместима: сервис добавит таблицу matches, а старые таблицы
перестанут использоваться. Для новой базы задайте DATABASE_PATH=/data/monitor.db.

## Тесты

~~~bash
python -m pip install -r requirements-dev.txt
pytest -q
~~~

## События в логах

- source_registered — источник подключён;
- match_detected — совпадение записано в очередь;
- match_dry_run — совпадение найдено в безопасном режиме;
- match_published — заявка опубликована;
- match_publish_failed — публикация не удалась;
- publish_flood_wait — Telegram временно ограничил отправку.
