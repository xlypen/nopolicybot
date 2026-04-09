from services.reactions import (
    TELEGRAM_REACTION_EMOJI,
    pick_allowed_emoji,
    sanitize_reaction_emoji,
)


def test_sanitize_reaction_emoji():
    allowed = {"👍", "🔥"}
    assert sanitize_reaction_emoji("👍", allowed) == "👍"
    assert sanitize_reaction_emoji("😏", allowed) == "👍"


def test_sanitize_strips_vs16_so_red_heart_matches():
    assert "❤" in TELEGRAM_REACTION_EMOJI
    assert sanitize_reaction_emoji("❤️", TELEGRAM_REACTION_EMOJI, fallback="👍") == "❤"


def test_pick_allowed_emoji_filters_invalid():
    allowed = {"👍", "🔥"}
    out = pick_allowed_emoji(["😏", "🔥"], allowed, fallback="👍")
    assert out in allowed
