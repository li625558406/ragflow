#!/usr/bin/env bash
# =============================================================================
# deploy-frontend.sh - 本地构建前端并部署到服务器
# 用法: bash deploy-frontend.sh [--build-only] [--no-build]
# =============================================================================
set -euo pipefail

SERVER_IP="47.98.102.55"
SSH_KEY="D:/AI/konus-key.pem"
SERVER_PATH="/home/bid-agent-konus/ragflow2"
CONTAINER_NAME="docker-ragflow-cpu-1"
WEB_DIR="web"

SSH_CMD="ssh -i $SSH_KEY -o StrictHostKeyChecking=no root@$SERVER_IP"
SCP_CMD="scp -i $SSH_KEY -o StrictHostKeyChecking=no -r"

BUILD_ONLY=false
NO_BUILD=false
for arg in "$@"; do
  case $arg in
    --build-only) BUILD_ONLY=true ;;
    --no-build)   NO_BUILD=true ;;
  esac
done

# Step 1: Build
if [ "$NO_BUILD" = false ]; then
  echo "==> Building frontend..."
  cd "$WEB_DIR"
  npm run build
  cd ..
  echo "==> Build complete: $WEB_DIR/dist/"
fi

if [ "$BUILD_ONLY" = true ]; then
  echo "==> Build-only mode, skipping deployment."
  exit 0
fi

# Step 2: Clean old dist and upload new one
echo "==> Cleaning old dist on server..."
$SSH_CMD "rm -rf $SERVER_PATH/$WEB_DIR/dist"
echo "==> Uploading web/dist/ to server..."
$SCP_CMD "$WEB_DIR/dist" "root@$SERVER_IP:$SERVER_PATH/$WEB_DIR/"

# Step 3: Upload nginx config (in case it changed)
echo "==> Uploading nginx config..."
$SCP_CMD "docker/nginx/ragflow.conf.python" "root@$SERVER_IP:$SERVER_PATH/docker/nginx/ragflow.conf.python"

# Step 4: Upload docker-compose (in case it changed)
echo "==> Uploading docker-compose.yml..."
$SCP_CMD "docker/docker-compose.yml" "root@$SERVER_IP:$SERVER_PATH/docker/docker-compose.yml"

# Step 5: Copy ragflow.conf.python -> ragflow.conf and reload nginx
echo "==> Applying nginx config and reloading..."
$SSH_CMD "docker exec $CONTAINER_NAME cp -f /etc/nginx/conf.d/ragflow.conf.python /etc/nginx/conf.d/ragflow.conf && \
  docker exec $CONTAINER_NAME nginx -s reload" 2>/dev/null || \
  (echo 'Nginx reload failed, restarting container...' && \
   $SSH_CMD "cd $SERVER_PATH/docker && docker compose restart ragflow-cpu")

echo ""
echo "==> Deployment complete!"
echo "    C-end:  http://$SERVER_IP/"
echo "    Admin:  http://$SERVER_IP/5d41402abc4b2a76b9719d911017c592/"
