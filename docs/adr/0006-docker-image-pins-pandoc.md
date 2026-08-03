# The Docker image pins pandoc, and that is the point

The converter invokes pandoc with `--split-level`, a flag introduced in pandoc
3.0. Debian 12 ships pandoc 2.17, so `apt install pandoc` produces a box on which
conversion fails — which is why a 33 MB `.deb` was checked into the repository
root by hand. Pinning pandoc 3.10.1 and a known ImageMagick in the image turns a
recurring environment problem into a line in a Dockerfile.

Deployment via Docker Compose and GHCR is a consequence of this, not the reason
for it. Do not "simplify" the image away in favour of distro packages.
