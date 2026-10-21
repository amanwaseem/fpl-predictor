# The API's image: `docker compose up` runs it beside Postgres (#81), and #82
# deploys it.
#
# Two stages: the first installs the pinned dependencies and the package into
# a virtualenv; the second copies only that virtualenv into a slim image that
# runs as an unprivileged user. No compilers, no source tree, no data.

FROM python:3.13.7-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY requirements.txt requirements-db.txt requirements-api.txt pyproject.toml README.md ./
COPY fpl ./fpl
# requirements.txt ends in `-e .`, the editable install for development. The
# image installs the package itself instead, so drop that line first.
RUN python -m venv /venv \
 && sed -i '/^-e \.$/d' requirements.txt \
 && /venv/bin/pip install -r requirements-api.txt \
 && /venv/bin/pip install --no-deps .

FROM python:3.13.7-slim
RUN useradd --system --uid 10001 --no-create-home fpl
COPY --from=build /venv /venv
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER fpl
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["fpl-serve", "--host", "0.0.0.0", "--port", "8000"]
