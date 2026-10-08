#!/bin/bash
# The lab (SYNTHETIC-IMAGES-020): a long-running web service, unlike synthetic-images.sh which
# runs the batch. Same image, same .env, same /shared mount; a different container name.
#
# Network: host networking like the other stacks (the database is on the host loopback), but the
# service listens on the Docker bridge address only (LAB_HOST=172.17.0.1), so NGINX in its
# container reaches it at http://172.17.0.1:8195 and nothing outside the host does. Basic auth
# is NGINX's job (reverseproxy, location /synthetic-review/).
#
# Prerequisite once: 03_candidates_migration.sql (see README).
# Rebuild after a `git pull`: bash synthetic-images-lab.sh --restart

NAME=synthetic-images-lab
if [ "$1" = "--restart" ]; then
    docker stop "$NAME" >/dev/null 2>&1
fi
if [ "$(docker ps -q -f name=^${NAME}$)" ]; then
    echo "$NAME is already running (use --restart after a git pull)."
    exit 0
fi
mkdir -p $HOME/docker/shared_data/synthetic-images
cd $HOME/docker/synthetic-images
docker build -t synthetic-images-python-app .
docker run -d --rm --network="host" --name "$NAME" \
    --env-file /home/debian/docker/synthetic-images/.env \
    -e LAB_HOST=172.17.0.1 -e LAB_PORT=8195 \
    -v $HOME/docker/shared_data/synthetic-images:/shared \
    synthetic-images-python-app python ./synthetic_images_lab.py
echo "$NAME started: https://www.vaugouin.com/synthetic-review/"
