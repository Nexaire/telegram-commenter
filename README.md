# Полуавтоматический Telegram-комментатор (MVP)

Сервис отслеживает новые посты в заданных публичных каналах через пользовательский Telegram-аккаунт, отбирает их с помощью GigaChat, генерирует два варианта экспертного комментария и отправляет карточку в приватного approval-бота. После подтверждения комментарий попадает в устойчивую SQLite-очередь и публикуется с задержкой в ветку обсуждения поста.

По умолчанию включён `DRY_RUN=true`: весь поток работает, но реального сообщения в Telegram не будет.

## Что реализовано

- события Telethon плюс периодический catch-up за заданное окно;
- дедупликация по `(channel_id, message_id)` в SQLite;
- два варианта и кнопки «Опубликовать», «Другой вариант», «Пропустить»;
- очередь публикации, переживающая рестарты;
- суточный лимит и случайная задержка;
- фильтр чёрных тем в промпте и строгая локальная блокировка ссылок, `@mention` и названий брендов;
- обработка `FLOOD_WAIT` с переносом задачи, а также ошибок запрета записи/бана/приватного канала;
- публикация от пользовательского аккаунта либо через `SEND_AS_CHANNEL`, если Telegram разрешает этому аккаунту писать от имени указанного канала;
- JSON-логи, Dockerfile и Compose.

## Ограничения Telegram

Это MTProto-клиент пользовательского аккаунта, а не только Bot API. Аккаунт должен видеть каждый отслеживаемый канал и иметь право писать в его связанную группу обсуждений. Комментарии должны быть включены. `SEND_AS_CHANNEL` не гарантируется: целевая группа должна разрешать отправку от имени каналов, а указанный канал должен присутствовать среди допустимых `send_as` для аккаунта.

Не повышайте лимиты резко, не добавляйте рекламу и не пытайтесь обходить `FLOOD_WAIT`. Автоматизация может привести к ограничениям аккаунта или бану администраторами групп. Для MVP рекомендуется отдельный живой аккаунт, 10–20 каналов, ручное подтверждение и 3–5 комментариев в сутки.

## 1. Telegram API ID и hash

1. Войдите на [my.telegram.org](https://my.telegram.org/) номером технического пользовательского аккаунта.
2. Откройте **API development tools** и создайте приложение.
3. Скопируйте `api_id` и `api_hash` в `.env`. Никому их не передавайте.
4. Включите двухфакторную аутентификацию аккаунта.

API ID/hash относятся к пользовательскому Telethon-клиенту. Bot token их не заменяет.

## 2. Approval-бот и ID согласующих

1. Создайте бота через `@BotFather`, получите token и задайте `APPROVAL_BOT_TOKEN`.
2. Напишите созданному боту `/start`, иначе он не сможет первым отправить вам карточку.
3. Узнайте свой числовой Telegram user ID (например, через `@userinfobot`) и задайте `APPROVER_USER_IDS`. Несколько ID перечисляются через запятую.

Только эти ID могут нажимать callback-кнопки. Команды для посторонних пользователей ничего не публикуют.

## 3. GigaChat API

1. Откройте [личный кабинет GigaChat API](https://developers.sber.ru/portal/products/gigachat-api).
2. Создайте проект GigaChat API.
3. В настройках API нажмите «Получить ключ» и сохраните **Authorization Key**. Он показывается только один раз.
4. Укажите его в `.env`. Нужен именно Authorization Key, а не временный access token: официальный SDK самостоятельно получает и обновляет токен.

Для физического лица:

```env
GIGACHAT_CREDENTIALS=ваш_Authorization_Key
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-2
GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_VERIFY_SSL_CERTS=false
GIGACHAT_CA_BUNDLE_FILE=
```

Для ИП/юрлица по предоплате используйте `GIGACHAT_API_B2B`, по постоплате — `GIGACHAT_API_CORP`; scope должен соответствовать выданному ключу. Доступные вашему проекту модели можно посмотреть в кабинете или через API `/models`.

`GIGACHAT_VERIFY_SSL_CERTS=false` упрощает первый запуск, но отключает проверку TLS-сертификата. Для постоянной эксплуатации скачайте корневой сертификат НУЦ Минцифры, смонтируйте его в контейнер, задайте `GIGACHAT_CA_BUNDLE_FILE` и переключите проверку на `true`.

После заполнения `.env` отдельно проверьте ключ и модель:

```bash
docker compose build
docker compose run --rm commenter python -m app.check_gigachat
```

Успешный результат содержит `GigaChat connection OK`. Ошибка `401` означает неверный Authorization Key или scope, `403` — отсутствие доступа, а ошибка TLS — необходимость установить сертификат НУЦ Минцифры либо временно оставить `GIGACHAT_VERIFY_SSL_CERTS=false`.

## 4. Конфигурация

```powershell
Copy-Item .env.example .env
```

Отредактируйте `.env`, затем `config/channels.yaml`:

```yaml
channels:
  - username: public_channel_username
    enabled: true
    expertise: "AI, автоматизация и бизнес-процессы"
  - username: another_channel
    enabled: true
    expertise: "управление продуктом"
```

Username указывается без `https://t.me/` и без обязательного `@`. Приватный канал возможен только если пользовательский аккаунт уже имеет к нему доступ; можно указать доступный entity ID.

Важные переменные:

- `DRY_RUN=true` — безопасный тест без отправки;
- `DAILY_COMMENT_LIMIT=5` — число подтверждённых/отправленных задач за текущие UTC-сутки;
- `PUBLISH_DELAY_MIN_SECONDS` / `MAX` — случайная задержка после подтверждения;
- `BLACKLIST_TOPICS` — темы через запятую; LLM должна пропускать такие посты;
- `BRAND_NAMES` — названия брендов через запятую, запрещённые локальным валидатором;
- `SEND_AS_CHANNEL` — необязательный username вашего канала. Пустое значение отправляет от пользователя;
- `MONITOR_LOOKBACK_HOURS` — окно catch-up после простоя;
- `MONITOR_POLL_SECONDS` — период дополнительной сверки.

Чёрный список является смысловым LLM-фильтром, а запрет ссылок/упоминаний/брендов дополнительно проверяется локально. Любая невалидная генерация получает статус `error` и не попадает на согласование.

## 5. Создание Telethon session

Сессия содержит авторизацию пользовательского аккаунта и является секретом уровня пароля. Не коммитьте и не копируйте её третьим лицам.

С Docker выполните интерактивно (потребуются номер, код Telegram и пароль 2FA):

```bash
docker compose build
docker compose run --rm commenter python -m app.init_session
```

Файл появится в `./data/commenter.session`. Затем запускайте сервис обычным способом. При запросе кода только на каждом старте сессия не сохраняется — проверьте права на `data/` и значение `TELEGRAM_SESSION=/data/commenter.session`.

Без Docker:

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.init_session
python -m app.main
```

Для локального запуска поменяйте пути в `.env` на `./data/commenter.session`, `./data/commenter.db` и `./config/channels.yaml`.

## 6. Безопасная проверка

1. Оставьте `DRY_RUN=true`.
2. Добавьте один тестовый канал с открытыми комментариями.
3. Запустите `docker compose up` и дождитесь карточки.
4. При необходимости переключите или отредактируйте вариант, подтвердите и проверьте лог `dry_run_publish` и запись в БД со статусом `dry_run`.
5. Только после проверки задайте `DRY_RUN=false` и перезапустите контейнер.

Логи:

```bash
docker compose logs -f --tail=200 commenter
```

SQLite хранится в `./data/commenter.db`. Статусы: `pending`, `skipped`, `scheduled`, `published`, `deleted`, `dry_run`, `filtered`, `permission_error`, `error`.

После реальной публикации сервис сохраняет ID комментария и проверяет его наличие каждые `PUBLISHED_AUDIT_SECONDS` секунд. Удалённые комментарии получают статус `deleted`. Ежедневный отчёт за завершившиеся сутки отправляется approval-ботом в `DAILY_REPORT_HOUR:DAILY_REPORT_MINUTE` часового пояса `DAILY_REPORT_TIMEZONE` (по умолчанию в 00:05 по Москве).

## 7. Развёртывание на Ubuntu VPS

Установите Docker Engine и Compose plugin из официального репозитория Docker. Затем:

```bash
sudo mkdir -p /opt/telegram-commenter
sudo chown "$USER":"$USER" /opt/telegram-commenter
cd /opt/telegram-commenter
# Скопируйте файлы проекта сюда через git/scp, затем:
cp .env.example .env
mkdir -p data
chmod 700 data
chmod 600 .env
docker compose build
docker compose run --rm commenter python -m app.init_session
docker compose up -d
docker compose logs -f --tail=200 commenter
```

Если bind mount на Linux не позволяет UID `10001` писать в `data`, выполните `sudo chown -R 10001:10001 data`. Настройте firewall, SSH-ключи и регулярную зашифрованную резервную копию `data/commenter.db`; файл session резервируйте только в защищённое хранилище. Входящие порты сервису не нужны: оба Telegram-клиента используют исходящие соединения.

Обновление:

```bash
docker compose build --pull
docker compose up -d
```

## Поведение при ошибках

- `FLOOD_WAIT`: задача остаётся `scheduled` и переносится на требуемое Telegram время плюс 5 секунд.
- Нет прав/бан/приватность: `permission_error`, повторной автоматической отправки нет.
- Прочая ошибка отправки: `error`; текст сохраняется в поле `error`.
- Рестарт: все наступившие `scheduled` задачи подбираются фоновым worker.
- Повторно найденный пост: SQLite unique constraint не позволяет снова вызвать LLM.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest -q
```

Перед реальной эксплуатацией полезно добавить интеграционный тест на вашем тестовом канале и связанной группе: конкретные права `send_as` нельзя надёжно проверить без Telegram-окружения владельца.
