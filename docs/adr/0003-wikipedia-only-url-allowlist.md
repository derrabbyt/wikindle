# Pasted URLs are restricted to Wikipedia by allowlist

Subscribers can ask us to convert a link they supply, which means a public
endpoint causes our server to fetch a URL a stranger chose — a server-side
request forgery hole, made worse by PostgreSQL and the API sharing the same host.
Accepted URLs must match `https://<lang>.wikipedia.org/wiki/...` as an allowlist,
never a blocklist, with the scheme pinned and redirects to off-allowlist hosts
refused.

The restriction is also a correctness one. The converter's selectors, infobox
flattening and citation handling are Wikipedia-specific: wikivoyage — a sister
project using the same skin — crashes it outright, and a Fandom wiki yields 53
missing images and 131 dropped icons.

Widening this is not a configuration change. It requires splitting the converter
into a source adapter — which, given a URL, returns cleaned article HTML, a title
and a language, and is the only place site-specific knowledge lives — and a
source-agnostic EPUB backend handling transcoding, chapter splitting, the cover
and pandoc. It must not be done casually.
