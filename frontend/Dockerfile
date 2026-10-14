# RoutePulse frontend — production Docker image
#
# Multi-stage build:
#   Stage 1 (builder): npm install + vite build
#   Stage 2 (runtime): Nginx serves the static dist/

# ---------------------------------------------------------------------------
# Stage 1 — build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first (better layer caching)
COPY package.json package-lock.json* ./
RUN npm ci --prefer-offline

# Copy source and build.
# VITE_API_URL can be injected at build time via --build-arg:
#   docker build --build-arg VITE_API_URL=https://api.example.com/api .
ARG VITE_API_URL=http://localhost:8000/api
ENV VITE_API_URL=$VITE_API_URL

COPY . .
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — Nginx runtime
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Remove the default Nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy our SPA-friendly config
COPY nginx.conf /etc/nginx/conf.d/routepulse.conf

# Copy the built assets from stage 1
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
