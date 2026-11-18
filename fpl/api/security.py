"""Security headers on every response (#178).

The site and the API share one origin (ADR 0025), so one policy covers both:
everything comes from this origin except the player photos, which the site
loads from the Premier League's own image server (web/src/components/bits.tsx).
React sets inline styles through the DOM, which a style-src of 'self' allows.

/docs is FastAPI's Swagger UI, which loads its script and stylesheet from
jsDelivr and starts itself with an inline script, so it gets a policy of its
own that allows exactly that.

HSTS only when hosted (FPL_HOSTED, set in fly.toml), where Fly already
redirects http to https: a local `make serve` is plain http on 127.0.0.1,
and a browser that remembered HSTS for it would refuse it afterwards.
"""

from os import environ

from fastapi import Request

PLAYER_PHOTOS = "https://resources.premierleague.com"
SWAGGER_CDN = "https://cdn.jsdelivr.net"
SWAGGER_FAVICON = "https://fastapi.tiangolo.com"

SITE_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"img-src 'self' data: {PLAYER_PHOTOS}",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)
DOCS_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {SWAGGER_CDN}",
        f"style-src 'self' {SWAGGER_CDN}",
        f"img-src 'self' data: {SWAGGER_FAVICON}",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)
DOCS_PATHS = ("/docs", "/docs/oauth2-redirect")
HSTS = "max-age=31536000"


async def security_headers(request: Request, call_next):
    """The headers above, on whatever the rest of the app answered."""
    response = await call_next(request)
    docs = request.url.path in DOCS_PATHS
    response.headers["Content-Security-Policy"] = DOCS_POLICY if docs else SITE_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if environ.get("FPL_HOSTED") == "1":
        response.headers["Strict-Transport-Security"] = HSTS
    return response
