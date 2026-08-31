FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend ./
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_DATA_MODE=api
ARG VITE_ICP_NUMBER=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_DATA_MODE=$VITE_DATA_MODE
ENV VITE_ICP_NUMBER=$VITE_ICP_NUMBER
RUN pnpm build

FROM caddy:2.10.2-alpine
RUN addgroup -S -g 10000 medtrust \
    && adduser -S -D -H -u 10000 -G medtrust medtrust \
    && setcap -r /usr/bin/caddy
COPY gateway/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /frontend/dist /srv
USER 10000:10000
EXPOSE 8080
