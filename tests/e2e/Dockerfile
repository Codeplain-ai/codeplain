# E2E test base image for codeplain CLI.
#
# install.sh is intentionally not executed but only COPIED
# Each pytest test runs install.sh inside a container via `docker exec`, so the installation is re-tested on every run. This must not be cached.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install only the OS tools install.sh needs to bootstrap uv. `uv tool install
# codeplain` will download its own Python 3.11. We just need *some* python3 on PATH to execute the rendered hello_world.py
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        python3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash e2e
USER e2e
WORKDIR /home/e2e

COPY --chown=e2e:e2e install/bash/install.sh /home/e2e/install.sh

ENV PATH="/home/e2e/.local/bin:${PATH}"

RUN mkdir -p /home/e2e/work
WORKDIR /home/e2e/work

CMD ["tail", "-f", "/dev/null"]
