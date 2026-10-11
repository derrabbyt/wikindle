---
status: accepted
---

# CI rolls the server out over SSH, through the tunnel

A green run on `main` now ends by SSHing into the VPS and running
`deploy/rollout.sh`. Until now CI stopped at publishing the image and the box
was updated by hand.

The connection goes through the same Cloudflare Tunnel as the API, so ADR 0001
still holds: the box accepts no inbound connections and no port 22 is open to
the internet.

## Considered options

A timer on the box that pulls for itself needs no credentials anywhere and was
the smaller change. It was passed over because a rollout would then be invisible
to CI: a run could go green while the box failed to start the new image.

A self-hosted Actions runner on the VPS was rejected outright. The repository is
public, and a runner on the box that holds the database is one misconfigured
workflow away from running a stranger's pull request.

## What stands between GitHub and the box

Three things, each of which fails closed on its own:

- **Cloudflare Access** in front of the SSH hostname, with a Service Auth policy
  that admits one service token and no humans.
- **A dedicated SSH key** whose `authorized_keys` entry is `restrict`ed and
  carries a forced command, so it can run the rollout script and nothing else —
  no shell, no port forwarding, no choice of arguments.
- **The script itself**, which only pulls and restarts `api` and `scheduler`.

## Consequences

GitHub now holds credentials that can restart production. They cannot read the
database or the `.env`, but the key belongs to the user that owns the checkout,
which is root on the box if the forced command is ever removed.

The tunnel and the database are deliberately out of the script's reach — the SSH
session rides the tunnel container, so recreating it mid-rollout would cut the
rollout off. Changes to those two, and every migration, are still applied by
hand. An image that needs a schema change will now roll out before anyone has
applied it.
