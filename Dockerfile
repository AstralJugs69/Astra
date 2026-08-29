# Cloud Run image recipe. Gate 0 must bind the profile table hashes before this
# image can report ready for generation.
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS build

ARG LIBLOUIS_VERSION=3.38.0
ARG LIBLOUIS_COMMIT=07c61e58cfb8814f6842c7212063f829288638c1
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       autoconf automake build-essential ca-certificates gettext git libtool pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/liblouis
RUN git clone --depth 1 --branch v${LIBLOUIS_VERSION} https://github.com/liblouis/liblouis.git . \
    && test "$(git rev-parse HEAD)" = "${LIBLOUIS_COMMIT}" \
    && ./autogen.sh \
    && ./configure --prefix=/opt/liblouis \
    && make -j2 \
    && make install \
    && python -m pip install --no-cache-dir --target=/opt/liblouis-python /tmp/liblouis/python

FROM build AS application
COPY --from=ghcr.io/astral-sh/uv:0.6.8@sha256:cb641b1979723dc5ab87d61f079000009edc107d30ae7cbb6e7419fdac044e9f /uv /uvx /usr/local/bin/
ENV UV_HTTP_TIMEOUT=600 \
    UV_HTTP_RETRIES=20 \
    UV_CONCURRENT_DOWNLOADS=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --frozen --no-dev --no-install-project
COPY src /app/src
COPY config /app/config
COPY schemas /app/schemas
COPY infra/scripts/bind_liblouis_profile.py /app/infra/scripts/bind_liblouis_profile.py
RUN uv pip install --python /app/.venv/bin/python --no-deps /app
RUN PYTHONPATH=/opt/liblouis-python \
    LD_LIBRARY_PATH=/opt/liblouis/lib \
    LIBLOUIS_TABLEPATH=/opt/liblouis/share/liblouis/tables \
    /app/.venv/bin/python infra/scripts/bind_liblouis_profile.py

FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/opt/liblouis-python \
    LD_LIBRARY_PATH=/opt/liblouis/lib \
    LIBLOUIS_TABLEPATH=/opt/liblouis/share/liblouis/tables \
    RELAY_CONFIG_ROOT=/app/config \
    TRANSLATION_PROFILE_PATH=/app/config/translation_profiles/demo-ueb-40x25-v1.json \
    RELAY_RECOMMENDATION_POLICY=/app/config/policies/recommendation.v1.json

COPY --from=build /opt/liblouis /opt/liblouis
COPY --from=build /opt/liblouis-python /opt/liblouis-python
COPY --from=application /app/.venv /app/.venv
COPY config /app/config
COPY schemas /app/schemas
COPY --from=application /app/work/translation-profile.bound.json /app/config/translation_profiles/demo-ueb-40x25-v1.json

WORKDIR /app
EXPOSE 8080
CMD ["uvicorn", "braille_errata_relay.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
