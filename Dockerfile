# The API's image: `docker compose up` runs it beside Postgres (#81), and #82
# deploys it.
#
# Two stages: the first installs uv.lock's dependencies and the package into
# a virtualenv; the second copies only that virtualenv into a slim image that
# runs as an unprivileged user. No compilers, no source tree, no data.

FROM python:3.13.7-slim AS build
# uv, pinned like CI's, copied from its release image rather than installed.
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
# The interpreter is the image's, and the virtualenv lives where the second
# stage expects it.
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/venv UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY fpl ./fpl
# Exactly the lock's versions (--frozen), the api group and nothing for
# development, and the package itself installed rather than editable: the
# image has no checkout to point an editable install at (ADR 0015).
RUN uv sync --frozen --no-dev --group api --no-editable

FROM python:3.13.7-slim
RUN useradd --system --uid 10001 --no-create-home fpl
COPY --from=build /venv /venv
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER fpl
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["fpl-serve", "--host", "0.0.0.0", "--port", "8000"]
