# Baseline Contracts

This file fixes behavior contracts used during deep refactor.

## End-to-end chains

1. Incoming group message -> `bot.check_and_reply` -> `add_to_history` -> `user_stats.record_chat_message`.
2. Directed message to bot -> `bot.on_message_to_bot` -> AI routing (`kind` / `technical` / `substantive` / `rude`) -> reply.
3. Photo path -> `ai_analyzer.analyze_image` -> optional archive write -> optional reaction.
4. Admin API routes -> modify JSON state only through module functions.
5. Social graph path -> `social_graph.append_dialogue_message` (online) -> `process_pending_days` (batch).

## Data invariants

- `user_stats.json` contains top-level keys: `users`, `chats`.
- Every user has `stats.total_messages`, `messages_by_chat`, `images_archive`.
- `question_of_day_tracking.json` has `by_reply` and `by_user_private`.
- `social_graph.json` has `dialogue_log`, `processed_dates`, `connections`.

## Enum contracts

- Sentiment values: `positive`, `negative`, `neutral`.
- Message type values: `technical_question`, `general_question`, `other`.
- Batch style values: `moderate`, `active`, `beast`.

## Safety constraints

- Reactions must stay inside Telegram allowed set.
- Unknown/invalid AI output must fallback to safe defaults.
- Refactor must preserve existing API endpoints in `admin_app.py`.
