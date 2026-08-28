# Cloud Run image recipe. Gate 0 must bind the profile table hashes before this
# image can report ready for generation.
FROM python:3.12-slim AS build

ARG LIBLOUIS_VERSION=3.38.0
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       autoconf automake build-essential ca-certificates gettext git libtool pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/liblouis
RUN git clone --depth 1 --branch v${LIBLOUIS_VERSION} https://github.com/liblouis/liblouis.git . \
    && ./autogen.sh \
    && ./configure --prefix=/opt/liblouis --enable-python-bindings \
    && make -j2 \
    && make install

RUN python -m pip install --no-cache-dir /tmp/liblouis/python

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIBLOUIS_TABLEPATH=/opt/liblouis/share/liblouis/tables

COPY --from=build /opt/liblouis /opt/liblouis
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config /app/config

WORKDIR /app
RUN python -m pip install --no-cache-dir --no-deps .
EXPOSE 8080
CMD ["uvicorn", "braille_errata_relay.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
