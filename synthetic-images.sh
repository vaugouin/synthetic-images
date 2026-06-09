#!/bin/bash

# Check if the synthetic-images Docker container is running
if [ $(docker ps -q -f name=synthetic-images) ]; then
    echo "synthetic-images Docker container is already running."
else
    # Start the synthetic-images container if it is not running.
    # Its own shared_data subdir holds the generated WebP masters; tmdb-front's
    # Apache serves them from $HOME/docker/shared_data/synthetic-images (review §10).
    mkdir -p $HOME/docker/shared_data/synthetic-images
    cd $HOME/docker/synthetic-images
    docker build -t synthetic-images-python-app .
    # Interactive (single-item harness / dry-run dev loop):
    # docker run -it --rm --network="host" --name synthetic-images --env-file /home/debian/docker/synthetic-images/.env -v $HOME/docker/shared_data/synthetic-images:/shared synthetic-images-python-app python ./synthetic-images.py --item-wikidata Q0 --dry-run
    docker run -d --rm --network="host" --name synthetic-images --env-file /home/debian/docker/synthetic-images/.env -v $HOME/docker/shared_data/synthetic-images:/shared synthetic-images-python-app
    echo "synthetic-images Docker container started."
fi
