# Collecct frontend — Next.js 15 / React 19 (mirrors the PriceIQ frontend image)
FROM node:20-alpine

WORKDIR /app

# Install dependencies. --legacy-peer-deps for React 19 peer-dependency compatibility.
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps

# Application source
COPY . .

# Backend URL — baked at BUILD time (Next inlines NEXT_PUBLIC_*). On Railway set a service
# variable NEXT_PUBLIC_API_BASE; Railway passes it to this ARG during the Docker build.
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE

# Next 15 builds can be memory-hungry.
ENV NODE_OPTIONS="--max-old-space-size=4096"

RUN npm run build

EXPOSE 3000

# Railway injects $PORT and proxies to it; default 3000 locally.
CMD ["sh", "-c", "node_modules/.bin/next start -p ${PORT:-3000}"]
