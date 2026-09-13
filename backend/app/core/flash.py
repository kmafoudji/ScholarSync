"""
Messages flash — notifications persistées entre deux requêtes.

Le message est déposé dans un cookie court signé HMAC, lu au rendu du
template suivant, puis effacé par FlashMiddleware. Signé pour qu'un tiers
ne puisse pas injecter de texte arbitraire dans la zone de notification.
"""
import base64
import hashlib
import hmac
import json
from typing import Optional

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.core.config import settings

COOKIE_NAME = "scholarsync_flash"
COOKIE_MAX_AGE = 30  # secondes : le temps d'une redirection

# Types reconnus par le front (classes CSS + icône)
TYPES = ("success", "info", "warning", "danger")


def _sign(payload: bytes) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), payload, hashlib.sha256
    ).hexdigest()[:32]


def _encode(message: str, type_: str) -> str:
    payload = json.dumps(
        {"m": message, "t": type_}, ensure_ascii=False
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode("ascii")
    return f"{body}.{_sign(payload)}"


def _decode(raw: str) -> Optional[dict]:
    try:
        body, signature = raw.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(body.encode("ascii"))
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return None

    message = str(data.get("m", ""))[:400]
    type_ = data.get("t", "success")
    if not message:
        return None
    return {"message": message, "type": type_ if type_ in TYPES else "success"}


def set_flash(response: Response, message: str, type_: str = "success") -> Response:
    """Attache un message flash à une réponse déjà construite."""
    response.set_cookie(
        COOKIE_NAME,
        _encode(message, type_),
        max_age=COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return response


def redirect_flash(
    url: str, message: str, type_: str = "success", status_code: int = 303
) -> RedirectResponse:
    """Raccourci : redirige en affichant une notification sur la page d'arrivée."""
    return set_flash(RedirectResponse(url, status_code=status_code), message, type_)


def read_flash(request: Request) -> Optional[dict]:
    raw = request.cookies.get(COOKIE_NAME)
    return _decode(raw) if raw else None
