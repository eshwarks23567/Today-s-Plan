# Today's Plan. The backend has no Python dependencies, so this image is a base
# image plus the source — there is no pip install step at all.
#
# The one thing it DOES need is curl. fetch() shells out to it because
# BookMyShow's CDN rejects Python's TLS fingerprint, and python:slim does not
# ship curl. Leave it out and every crawl dies with "fetch failed (127)", which
# reads like an application bug rather than a missing binary.
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# server.py resolves the frontend as ../frontend from its own location, so these
# two directories have to keep this layout relative to each other.
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# $PORT is what tells server.py this is a shared host rather than someone's
# laptop: bind every interface, rate-limit on the proxy's forwarded client IP,
# and stop learning preferences into what is a single global file.
#
# Every PaaS injects its own value. The default matters for plain `docker run`,
# where an unset PORT would leave the server on loopback INSIDE the container,
# reachable by nothing.
ENV PORT=8765
EXPOSE 8765

# The crawl snapshots are the only thing written at runtime.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/backend/cache \
 && chown -R app:app /app/backend/cache
USER app

# -u so the crawl and error logs reach `docker logs` as they happen rather than
# sitting in a buffer until the process exits.
CMD ["python", "-u", "backend/server.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" > /dev/null || exit 1
