FROM node:20-slim AS builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --prefer-offline

COPY . .

ARG VITE_API_BASE_URL=/v1
ARG VITE_WS_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_WS_BASE_URL=$VITE_WS_BASE_URL

RUN npm run build

# Production: nginx serving static files
FROM nginx:1.27-alpine AS production

COPY --from=builder /app/dist /usr/share/nginx/html

# SPA routing — all paths serve index.html
RUN printf 'server {\n\
  listen 8080;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;\n\
  add_header X-Frame-Options DENY always;\n\
  add_header X-Content-Type-Options nosniff always;\n\
  location / {\n\
    try_files $uri $uri/ /index.html;\n\
  }\n\
  location /v1/ {\n\
    return 404;\n\
  }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
