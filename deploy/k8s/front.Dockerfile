# Strong Hajin 운영 프론트 이미지 (linux/arm64) — 빌드한 SPA 를 nginx 가 서빙한다.
#
# API·WebSocket 은 같은 호스트의 /api 로 인그레스가 백엔드에 나눈다. 그래서 빌드에 API 주소가 들어가지 않는다.
#
#   docker buildx build --platform linux/arm64 -f deploy/k8s/front.Dockerfile -t <registry>/strong-hajin-front:<tag> .
FROM node:22-bookworm-slim AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/k8s/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
