############################
# Dependencies stage
############################
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS deps

WORKDIR /app

COPY ./src/payment/package.json package.json
COPY ./src/payment/package-lock.json package-lock.json

RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev \
    && npm cache clean --force


############################
# Runtime stage
############################
FROM gcr.io/distroless/nodejs22-debian13:nonroot@sha256:4e4fb0ce55fd73901600796ef079a9490369d2515d7da31633a91608c82ca13b

WORKDIR /app

ENV NODE_ENV=production

COPY --from=deps /app/node_modules ./node_modules

COPY ./pb/demo.proto demo.proto

COPY ./src/payment/index.js ./
COPY ./src/payment/charge.js ./
COPY ./src/payment/logger.js ./

CMD [ "index.js"]
