#!/usr/bin/env bash
# Roll the server out on the VPS. CI runs this over SSH as the deploy key's
# forced command, so it takes no arguments and ignores whatever the client asks
# for — see docs/adr/0009-ci-rolls-out-over-ssh-through-the-tunnel.md.
#
# The braces make bash read the whole file before running any of it, because
# `git pull` below may replace this script underneath itself.
{
    set -euo pipefail

    # A cancelled CI run drops the SSH session; finish the rollout regardless
    # rather than leave the api and scheduler on different images.
    trap '' HUP

    cd "$(dirname "$(readlink -f "$0")")/.."

    git pull --ff-only

    # Only the two app services. The SSH session rides the tunnel container, so
    # recreating it here would cut the rollout off halfway; the tunnel and the
    # database are changed by hand.
    docker compose pull api scheduler
    docker compose up -d --no-deps api scheduler

    docker image prune -f >/dev/null

    exit 0
}
