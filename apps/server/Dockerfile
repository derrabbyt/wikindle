# The point of this image is the pinned pandoc. `apt install pandoc` on Debian 12
# gives 3.x-less 2.17, which does not understand --split-level and so cannot
# build our EPUBs at all. See docs/adr/0006-docker-image-pins-pandoc.md.
FROM python:3.13-slim-bookworm

ARG PANDOC_VERSION=3.10.1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-dejavu-core \
        imagemagick \
        librsvg2-bin \
    && curl -fsSL -o /tmp/pandoc.deb \
        "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-amd64.deb" \
    && (dpkg -i /tmp/pandoc.deb || apt-get install -y -f --no-install-recommends) \
    && rm /tmp/pandoc.deb \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    # Fail the build rather than ship an image whose pandoc cannot split chapters.
    && pandoc --version | head -1 \
    && pandoc --help | grep -q -- --split-level

# ImageMagick's stock policy refuses to rasterise SVG, which is most of the
# diagrams and maps in a Wikipedia article. The path only exists once the
# package is installed, hence the guard.
RUN test -f /etc/ImageMagick-6/policy.xml \
    && sed -i 's|<policy domain="coder" rights="none" pattern="SVG" />|<policy domain="coder" rights="read\|write" pattern="SVG" />|' \
        /etc/ImageMagick-6/policy.xml \
    || echo "no ImageMagick policy to relax"

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --system --create-home --uid 10001 wikindle \
    && mkdir -p /var/lib/wikindle/epub \
    && chown -R wikindle:wikindle /var/lib/wikindle
USER wikindle

ENV WIKINDLE_STORAGE_DIR=/var/lib/wikindle/epub

EXPOSE 8000
CMD ["uvicorn", "wikindle.api:app", "--host", "0.0.0.0", "--port", "8000"]
