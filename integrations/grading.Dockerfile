FROM node:24.10.0-bookworm-slim AS node
FROM golang:1.27.2-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off
USER 65534:65534
WORKDIR /work
