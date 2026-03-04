def build_substantive_user_content(
    context: str,
    message_text: str,
    search_results: str = "",
    user_portrait: str = "",
) -> str:
    search_block = f"\n\n{search_results}\n---\n" if search_results else ""
    portrait_block = ""
    if user_portrait and user_portrait.strip():
        portrait_block = f"\n\nПортрет пользователя:\n{user_portrait.strip()}\n---\n"
    return f"""Диалог (хронологично, последнее сообщение — внизу):
{context}
{search_block}{portrait_block}Вопрос пользователя: {message_text}"""
