FROM node:22-alpine

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

ARG NEXT_PUBLIC_API_BASE=http://127.0.0.1:4317/console
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE

RUN npm run build
EXPOSE 4318
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "4318"]
