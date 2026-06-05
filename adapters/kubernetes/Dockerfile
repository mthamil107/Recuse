# Multi-stage build for the Recuse Kubernetes admission webhook.
#
# Stage 1 compiles a fully-static (CGO_ENABLED=0) linux/amd64 binary.
# Stage 2 is distroless:nonroot — no shell, no package manager, runs as an
# unprivileged user — so the runtime image has minimal attack surface.

# ---- build ---------------------------------------------------------------
FROM golang:1.26 AS build
WORKDIR /src

# Cache modules first.
COPY go.mod go.sum ./
RUN go mod download

# Then the sources.
COPY *.go ./

# Static, trimmed, stripped binary. CGO off so it runs on distroless/scratch.
ARG TARGETOS=linux
ARG TARGETARCH=amd64
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w" -o /out/recuse-webhook .

# ---- runtime -------------------------------------------------------------
# distroless static + nonroot: uid 65532, no shell, read-only-friendly.
FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=build /out/recuse-webhook /recuse-webhook

# TLS cert/key are mounted from the Secret at /etc/recuse/tls (see manifests).
# Policy config is provided via env from the ConfigMap.
USER 65532:65532
EXPOSE 8443
ENTRYPOINT ["/recuse-webhook"]
