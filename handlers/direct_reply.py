import user_stats


def build_reply_context_with_images(context: str, user_id: int) -> str:
    """
    Добавляет в контекст память по изображениям, если она есть.
    """
    images_memory = user_stats.format_images_archive_for_context(user_id)
    reply_context = context or "(нет контекста)"
    if images_memory:
        reply_context = images_memory + "\n\n---\nДиалог:\n" + reply_context
    return reply_context
