"""Browser request boundaries; proxy trust is configured at the server edge."""
from urllib.parse import urlsplit

from ..errors import ServiceError
from ..i18n import t
from .deps import COOKIE_NAME


def origin(value, *, referer=False):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or (not referer and (parsed.path not in ("", "/") or parsed.query or parsed.fragment))):
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def check_browser_request(request, cfg):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return  # Explicit credentials; current_user never falls back to a cookie.
    sent_origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie and not sent_origin and not referer and not request.headers.get("sec-fetch-site"):
        return  # Non-browser login clients can obtain a Bearer token.
    expected = origin(cfg.public_origin or str(request.base_url))
    actual = origin(sent_origin) if sent_origin is not None else origin(referer or "", referer=True)
    if actual is None or actual != expected or request.headers.get("sec-fetch-site") == "cross-site":
        raise ServiceError("csrf_origin", t("请求来源与当前站点不一致"), status=403,
                           next_step=t("请从当前站点重新打开页面；脚本请使用 Bearer 令牌"))
