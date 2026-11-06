# The API's image: `docker compose up` runs it beside Postgres (#81), and #82
# deploys it.
#
# Three stages: the first builds the web frontend (#121) into static files;
# the second installs uv.lock's dependencies and the package into a
# virtualenv; the last copies only the virtualenv and the built site into a
# slim image that runs as an unprivileged user. No compilers, no Node, no
# source tree, no data.

# The site, from package-lock.json exactly. Dependencies first, in a layer of
# their own, so a change to the site's source reinstalls nothing.
FROM node:24.22.1-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build

FROM python:3.13.17-slim AS build
# uv, pinned like CI's, copied from its release image rather than installed.
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
# The interpreter is the image's, and the virtualenv lives where the second
# stage expects it.
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/venv UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
# Exactly the lock's versions (--locked), the api group and nothing for
# development. The dependencies first, in a layer of their own, so a change to
# fpl/ reinstalls only the package; then the package itself, installed rather
# than editable: the image has no checkout to point an editable install at
# (ADR 0015).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --group api --no-install-project
COPY fpl ./fpl
RUN uv sync --locked --no-dev --group api --no-editable

FROM python:3.13.17-slim
RUN useradd --system --uid 10001 --no-create-home fpl
COPY --from=build /venv /venv
# Served by the API at / (ADR 0025).
COPY --from=web /web/dist /web
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FPL_WEB_DIR=/web
USER fpl
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["fpl-serve", "--host", "0.0.0.0", "--port", "8000"]
