"""OAuth desktop-flow и сборка клиента Drive API.

Поток:
  1. Если есть валидный кэшированный токен — используем его.
  2. Если токен протух, но есть refresh_token — обновляем молча.
  3. Иначе запускаем интерактивный InstalledAppFlow (откроется браузер),
     для чего нужен client_secret.json (OAuth client типа Desktop app).
"""

from __future__ import annotations

import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from . import config


class AuthError(Exception):
    """Проблема с аутентификацией (нет client_secret, отказ пользователя и т.п.)."""


def _load_cached() -> Credentials | None:
    path = config.token_path()
    if not path.is_file():
        return None
    return Credentials.from_authorized_user_file(str(path), config.SCOPES)


def _save(creds: Credentials) -> None:
    path = config.token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)


def get_credentials(*, interactive: bool = True) -> Credentials:
    """Вернуть валидные креды, при необходимости запустив OAuth-поток.

    interactive=False запрещает открывать браузер: подходит для cron/служб,
    где допустимо только молчаливое обновление по refresh_token.
    """
    creds = _load_cached()

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
        return creds

    if not interactive:
        raise AuthError(
            "Нет валидного токена и обновление невозможно. "
            "Запусти `gdrive-sync auth` интерактивно."
        )

    secret = config.client_secret_path()
    if not secret.is_file():
        raise AuthError(
            f"Не найден OAuth client_secret: {secret}\n"
            "Создай OAuth client (Desktop app) в Google Cloud Console, "
            "включи Drive API и положи скачанный JSON по этому пути "
            "(или укажи $GDRIVE_SYNC_CLIENT_SECRET)."
        )

    _check_client_secret_kind(secret)
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), config.SCOPES)
    creds = flow.run_local_server(port=0)
    _save(creds)
    return creds


def _check_client_secret_kind(secret) -> None:
    """Убедиться, что это OAuth client (Desktop/Web), а не, например, service account.

    Даёт понятную ошибку вместо сырого ValueError из библиотеки.
    """
    try:
        raw = json.loads(secret.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"Не удалось прочитать {secret}: {exc}") from exc

    if "installed" not in raw and "web" not in raw:
        kind = raw.get("type", "неизвестный тип")
        raise AuthError(
            f"{secret} — не OAuth client для Desktop app (обнаружено: {kind}).\n"
            "Нужен OAuth client ID типа «Desktop app» (в JSON ключ верхнего уровня "
            "'installed'), а не service account.\n"
            "Google Cloud Console → APIs & Services → Credentials → Create credentials "
            "→ OAuth client ID → Application type: Desktop app → скачать JSON и положить "
            f"в {secret}."
        )


def build_service(creds: Credentials) -> Resource:
    """Собрать клиент Drive API v3.

    cache_discovery=False — чтобы не плодить предупреждения и файловый кэш.
    """
    return build("drive", "v3", credentials=creds, cache_discovery=False)
