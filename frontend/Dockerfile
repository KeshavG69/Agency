# Collecct frontend — Next.js 15 / React 19 (mirrors the PriceIQ frontend image)
FROM node:20-alpine

WORKDIR /app

# Install dependencies. --legacy-peer-deps for React 19 peer-dependency compatibility.
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps

# Application source
COPY . .

# Backend URL is injected at RUNTIME by docker-entrypoint.sh (env API_BASE -> /__env.js),
# so no build-time API URL is needed. Next 15 builds can be memory-hungry.
ENV NODE_OPTIONS="--max-old-space-size=4096"

RUN npm run build

EXPOSE 3000

# Entrypoint injects the RUNTIME backend URL (env API_BASE) into /__env.js, then starts Next.
# So you can set the backend URL at deploy time WITHOUT rebuilding the image.
CMD ["sh", "docker-entrypoint.sh"]
