from aiogram.types import Message


def append_social_dialogue(message: Message, chat_id: int, first_name: str, display_text: str, social_graph, logger) -> None:
    """
    Лог диалогов для дерева связей (только группы, не от ботов).
    """
    if not message.chat or message.chat.type not in ("group", "supergroup"):
        return
    if not message.from_user or message.from_user.is_bot:
        return
    reply_to = message.reply_to_message
    reply_to_user_id = None
    if reply_to and reply_to.from_user and not getattr(reply_to.from_user, "is_bot", True):
        reply_to_user_id = reply_to.from_user.id
    try:
        social_graph.append_dialogue_message(
            chat_id=chat_id,
            sender_id=message.from_user.id,
            text=display_text,
            reply_to_user_id=reply_to_user_id,
            sender_name=first_name,
            chat_title=(message.chat.title or "") if message.chat else "",
        )
    except Exception as e:
        logger.debug("social_graph append: %s", e)
