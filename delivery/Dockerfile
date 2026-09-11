# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.13.15
ARG PYTHON_BASE=python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
ARG CODEX_BASE=node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e
ARG RUNTIME_BASE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171
ARG CODEX_VERSION=0.153.4

FROM ${PYTHON_BASE} AS compiler

ARG UV_VERSION=0.10.3

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential ccache patchelf \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /build/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --group build --no-install-project

COPY backend/src ./src
COPY delivery/strip_docstrings.py /build/strip_docstrings.py
RUN --mount=type=cache,target=/root/.cache/ccache \
    python /build/strip_docstrings.py src \
    && uv sync --frozen --no-dev --group build \
    && .venv/bin/python -m nuitka \
        --mode=standalone \
        --deployment \
        --output-dir=/build/output \
        --output-filename=scax \
        --include-package=ax_workspace \
        --include-package-data=kiwipiepy \
        --python-flag=dont_write_bytecode \
        --report-template=LicenseReport.rst.j2:/build/output/third-party-licenses.rst \
        --assume-yes-for-downloads \
        src/scax_protected.py \
    && test -x /build/output/scax_protected.dist/scax

FROM ${CODEX_BASE} AS codex

ARG CODEX_VERSION

RUN npm install --global "@openai/codex@${CODEX_VERSION}"

FROM ${RUNTIME_BASE} AS runtime

ARG PYTHON_VERSION
ARG CODEX_VERSION

LABEL org.scax.python.version="${PYTHON_VERSION}" \
    org.scax.nuitka.version="4.2.1" \
    org.scax.codex.version="${CODEX_VERSION}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libgomp1 tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 scax \
    && useradd --system --uid 10001 --gid scax --home-dir /var/lib/scax --create-home scax

COPY --from=compiler --chown=scax:scax /build/output/scax_protected.dist/ /opt/scax/
COPY --from=compiler /build/output/third-party-licenses.rst /usr/share/doc/scax/third-party-licenses.rst
COPY --from=codex /usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/ /opt/codex/

RUN test -x /opt/scax/scax \
    && test -z "$(find /opt/scax -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.c' -o -name '*.h' \) -print -quit)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCAX_RUNTIME_EXECUTABLE=/opt/scax/scax \
    PATH="/opt/codex/bin:${PATH}"

WORKDIR /var/lib/scax
USER scax
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/scax/scax"]
CMD ["api"]
