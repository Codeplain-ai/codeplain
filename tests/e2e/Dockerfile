# E2E test base image for codeplain CLI.
#
# Intentionally bare: install.sh is COPIED in but NOT executed here. Each pytest
# test runs install.sh fresh inside a container via `docker exec`, so the install
# flow is re-validated on every CI run (the layer cache won't skip it).

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install only the OS tools install.sh needs to bootstrap uv. `uv tool install
# codeplain` will download its own Python 3.11 (codeplain pins requires-python
# == 3.11) — we don't try to provide it ourselves. We just need *some* python3
# on PATH to execute the rendered hello_world.py output.
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
