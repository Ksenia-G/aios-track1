#!/bin/sh
# Собрать и запустить страницу на сервере. Всё своё лежит в /srv/aios-magic,
# чужие каталоги, контейнеры и конфиги nginx/caddy не трогаются.
#   /srv/aios-magic/app        -- код
#   /srv/aios-magic/materials  -- MODEL_Y и CHDD_PYTHON, только на чтение
#   /srv/aios-magic/llm.env    -- MAS_LLM_* и ключ, права 600
set -eu
ROOT=/srv/aios-magic
PORT="${PORT:-8765}"
SUBNET=172.30.77.0/24

docker build -t aios-magic:latest -f "$ROOT/app/deploy/public/Dockerfile" "$ROOT/app"
docker network inspect aios-magic-net >/dev/null 2>&1 \
  || docker network create --subnet "$SUBNET" aios-magic-net
# Своя сеть и правила: контейнер не видит ни хост, ни сети других сайтов.
install -m 0755 "$ROOT/app/deploy/public/firewall.sh" "$ROOT/firewall.sh"
install -m 0644 "$ROOT/app/deploy/public/aios-magic-firewall.service" \
  /etc/systemd/system/aios-magic-firewall.service
systemctl daemon-reload
systemctl enable --now aios-magic-firewall.service
systemctl restart aios-magic-firewall.service

docker rm -f aios-magic >/dev/null 2>&1 || true
# Памяти на сервере мало и swap нет: потолок держит OPM внутри контейнера,
# второе ядро остаётся соседним сайтам.
docker run -d --name aios-magic \
  --restart unless-stopped \
  --network aios-magic-net \
  -p "$PORT:8765" \
  --env-file "$ROOT/llm.env" \
  -v "$ROOT/materials:/materials:ro" \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 1200m --memory-swap 1200m --cpus 1.0 --pids-limit 256 \
  aios-magic:latest
docker ps --filter name=aios-magic --format '{{.Names}} {{.Status}} {{.Ports}}'
