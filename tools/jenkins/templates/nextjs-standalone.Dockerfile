# ===================================================================
# Dockerfile — Next.js 前后端不分离 SSR 应用 (standalone 多阶段)
# 生成: jenkins 技能 frontend-ssr 模板生成器同步输出
# 前置: next.config.js 已配置 output: "standalone" (next build 产出 .next/standalone)
# 运行: node server.js (PORT/HOSTNAME 环境变量控制)
# ===================================================================
FROM node:22-alpine AS base
WORKDIR /app

# ---- 依赖安装 (独立层, 利用缓存) ----
FROM base AS deps
COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com

# ---- 构建 (standalone 输出) ----
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- 运行镜像 (仅 standalone 精简产物) ----
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# standalone 服务端 (含精简 node_modules) + 静态资源 + public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

# 非 root 运行 (安全加固)
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs \
    && chown -R nextjs:nodejs /app
USER nextjs

EXPOSE 3000
CMD ["node", "server.js"]
