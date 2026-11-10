# TypeScript compiler runtime for the isolated CLICoding sandbox.
# Node.js 24 ARM64-capable official image, pinned by digest.
FROM node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9

ARG TYPESCRIPT_VERSION=7.0.2
RUN npm install --global --omit=dev typescript@${TYPESCRIPT_VERSION} \
    && test "$(tsc --version)" = "Version ${TYPESCRIPT_VERSION}"

WORKDIR /tmp
CMD ["tsc", "--version"]
