FROM node:22-alpine

ENV NODE_ENV=production
WORKDIR /app

COPY phase-1 ./phase-1
COPY phase-2 ./phase-2

WORKDIR /app/phase-2

USER node
EXPOSE 8080
CMD ["node", "src/server.js"]
