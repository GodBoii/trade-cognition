# =============================================================================
# Trade Cognition web app (Next.js, App Router)
#
# Multi-stage: dependencies -> build -> a minimal runtime carrying only the
# standalone server output.
# =============================================================================

# --- dependencies ------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci


# --- build -------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY package.json package-lock.json next.config.ts tsconfig.json ./
COPY app ./app
COPY components ./components
COPY lib ./lib
COPY state ./state
COPY public ./public

# Both of these are compiled into the build and cannot be changed afterwards.
#
#   API_PROXY_TARGET    where the Next server forwards /api/* (server-side, so
#                       a private hostname such as http://backend:8000 is fine)
#   NEXT_PUBLIC_API_URL set only when the *browser* should call the backend
#                       directly instead of going through this server
ARG API_PROXY_TARGET=http://backend:8000
ARG NEXT_PUBLIC_API_URL=""
ENV API_PROXY_TARGET=$API_PROXY_TARGET \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build


# --- runtime -----------------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# `output: "standalone"` produces a server with only the modules it needs.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
