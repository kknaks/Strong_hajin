# Strong Hajin 운영 백엔드 이미지 (linux/arm64, Mac Studio k8s).
#
# API 와 네 워커가 같은 이미지를 쓴다 — 차트가 command 만 바꾼다
# (charts/strong-hajin/templates/worker.yaml: python -m ax_workspace.entrypoints.<kind>_worker).
# codex·claude CLI 는 이미지에 넣지 않는다. 노드에 설치된 것을 hostPath 로 읽는다(mediness 와 같은 방식).
#
# 고객 납품용 amd64 보호 이미지(delivery/Dockerfile)와는 별개다.
#
#   docker buildx build --platform linux/arm64 -f deploy/k8s/back.Dockerfile -t <registry>/strong-hajin-back:<tag> .
ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# 의존성 층을 소스 층과 가른다 — 코드만 바뀐 빌드는 lockfile 층을 다시 쓴다.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:${PATH} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
EXPOSE 28080
ENTRYPOINT ["/usr/bin/tini", "--"]
# 인그레스(nginx) 뒤에 선다 — X-Forwarded-* 를 믿어야 https·client 주소가 맞는다.
CMD ["uvicorn", "ax_workspace.entrypoints.http:app", "--host", "0.0.0.0", "--port", "28080", "--proxy-headers", "--forwarded-allow-ips", "*"]
