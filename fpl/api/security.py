"""Security headers on every response (#178).

The site and the API share one origin (ADR 0025), so one policy covers both:
everything comes from this origin except the player photos, which the site
loads from the Premier League's own image server (web/src/components/bits.tsx).
React sets inline styles through the DOM, which a style-src of 'self' allows.

The Swagger UI at the app's docs_url loads its script and stylesheet from
jsDelivr and starts itself with an inline script, so its two pages get a
policy of their own that allows exactly swagger-ui-dist and that script. It
renders nothing but the OpenAPI document, so 'unsafe-inline' there admits no
reader's input; a hash would break with each FastAPI release that edits the
script. Its online validator badge is switched off in create_app, rather than
allowed here: it would send the document's URL to swagger.io.

HSTS only when hosted (FPL_HOSTED, set in fly.toml), where Fly already
redirects http to https: a local `make serve` is plain http on 127.0.0.1,
and a browser that remembered HSTS for it would refuse it afterwards.

A plain ASGI middleware, not @app.middleware("http"): it only adds headers to
the response's start message, and need not buffer or stream the body again.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PLAYER_PHOTOS = "https://resources.premierleague.com"
SWAGGER_DIST = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/"
SWAGGER_FAVICON = "https://fastapi.tiangolo.com"

COMMON = ["frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'", "object-src 'none'"]
SITE_POLICY = "; ".join(["default-src 'self'", f"img-src 'self' data: {PLAYER_PHOTOS}", *COMMON])
DOCS_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {SWAGGER_DIST}",
        f"style-src 'self' {SWAGGER_DIST}",
        f"img-src 'self' data: {SWAGGER_FAVICON}",
        *COMMON,
    ]
)
HSTS = "max-age=31536000"


class SecurityHeaders:
    """Adds the headers above to every HTTP response of `app`.

    `docs_paths` are the Swagger UI's pages, which get DOCS_POLICY; `hosted`
    says whether to send HSTS.
    """

    def __init__(self, app: ASGIApp, docs_paths: tuple[str, ...], hosted: bool):
        self.app = app
        self.docs_paths = docs_paths
        self.hosted = hosted

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        policy = DOCS_POLICY if scope["path"] in self.docs_paths else SITE_POLICY

        async def send_with_headers(message: Message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = policy
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                if self.hosted:
                    headers["Strict-Transport-Security"] = HSTS
            await send(message)

        await self.app(scope, receive, send_with_headers)
