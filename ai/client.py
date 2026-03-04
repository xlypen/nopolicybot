import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_project_env() -> None:
    load_dotenv(ENV_PATH, encoding="utf-8-sig", override=True)


def get_client() -> OpenAI:
    load_project_env()
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    if not api_key:
        raise ValueError("Не задан OPENAI_API_KEY в .env")
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)
