#!/bin/bash
# Roda periodicamente via cron no servidor. Se houver uma versão nova publicada no GitHub,
# atualiza o código e reinicia o serviço sozinho — ninguém (nem o TI) precisa entrar no
# servidor pra aplicar uma atualização normal do dia a dia.
#
# Instalação (como o usuário que roda o serviço, ex.: "raviera"):
#   1. Dê permissão de execução: chmod +x deploy/auto-update.sh
#   2. Libere o restart do serviço sem senha só pra esse comando (como root, uma vez):
#        echo "raviera ALL=(ALL) NOPASSWD: /bin/systemctl restart dashboard-raviera" \
#          | sudo tee /etc/sudoers.d/raviera-auto-update
#   3. Agende no cron do usuário "raviera" (crontab -e), rodando a cada 5 minutos:
#        */5 * * * * /opt/dashboard-mkt-raviera/deploy/auto-update.sh >> /var/log/raviera-auto-update.log 2>&1

set -e
cd "$(dirname "$0")/.."

git fetch origin main --quiet

LOCAL=$(git rev-parse main)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] nova versão detectada — atualizando..."
    git pull origin main
    sudo systemctl restart dashboard-raviera
    VERSAO=$(cat VERSION 2>/dev/null || echo "?")
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] atualizado para a versão $VERSAO"
fi
