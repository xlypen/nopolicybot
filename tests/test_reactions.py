from services.reactions import pick_allowed_emoji, sanitize_reaction_emoji


def test_sanitize_reaction_emoji():
    allowed = {"👍", "🔥"}
    assert sanitize_reaction_emoji("👍", allowed) == "👍"
    assert sanitize_reaction_emoji("😏", allowed) == "👍"


def test_pick_allowed_emoji_filters_invalid():
    allowed = {"👍", "🔥"}
    out = pick_allowed_emoji(["😏", "🔥"], allowed, fallback="👍")
    assert out in allowed
