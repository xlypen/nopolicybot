# Чеклист: добавление нового API endpoint

Выполнить каждый пункт по порядку. Не открывать PR без прохождения всего списка.

## 1. Определить принадлежность

- [ ] Где живёт логика: Flask или FastAPI?
  - **Flask:** бизнес-логика, работа с сессией, сложные запросы к DB через SQLAlchemy
  - **FastAPI:** realtime, WebSocket, тяжёлые async операции, внешний API

## 2. Обновить routing_map.md

- [ ] Добавить новый маршрут в таблицу `docs/routing_map.md`
- [ ] Указать: путь, upstream, auth, owner, описание

## 3. Реализовать endpoint

- [ ] Создать обработчик в нужном месте (Flask route или FastAPI router)
- [ ] Если FastAPI + нужна сессия → добавить Flask-прокси в `admin_app.py` через `proxy_to_fastapi()`
- [ ] Если Flask → добавить `@login_required` декоратор

## 4. Обновить nginx-конфиг

- [ ] Открыть `nginx/nopolicybot.conf`
- [ ] Нужен ли отдельный location блок? (WebSocket, особые таймауты, кэширование)
- [ ] Если да — добавить блок и обновить `docs/routing_map.md`
- [ ] Запустить `nginx -t -c nginx/nginx-test.conf` для проверки синтаксиса

## 5. Тесты

- [ ] Написать тест: endpoint возвращает 401 без аутентификации
- [ ] Написать тест: endpoint возвращает ожидаемый результат с аутентификацией
- [ ] Запустить `pytest -q` — нет новых failures

## 6. Документация

- [ ] Обновить `docs/routing_map.md` если не сделано в п.2
- [ ] Добавить endpoint в OpenAPI описание если FastAPI
- [ ] Если новый внешний API — обновить README секцию API
