# ===================================================================
# Dockerfile — Next.js standalone 运行时镜像（#215 优化：复用 Jenkins 已构建产物）
# 生成: jenkins 技能 frontend-ssr 模板生成器同步输出
# 前置：Jenkinsfile 的 "Next.js Build" 阶段已产出 .next/standalone + .next/static + public；
#       本 Dockerfile 仅"打包"，不再在镜像内重复 npm ci / next build（镜像更小、构建更快）。
# ===================================================================
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# 复用宿主已构建产物（context 由 Jenkinsfile 构建阶段产出）
COPY .next/standalone ./
COPY .next/static ./.next/static
COPY public ./public

# 非 root 运行（安全加固）
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs \
    && chown -R nextjs:nextjs /app
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
