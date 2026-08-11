#!/bin/bash
# Re-read albums.txt and pull a fresh photo list from Google.
# Run this after adding or removing album links.

cd "$(dirname "$0")" || exit 1

if curl -fsS -o /dev/null --max-time 3 http://localhost:8081/api/status 2>/dev/null; then
  # Service is running -- let it refresh so it picks the new list up immediately.
  curl -fsS -X POST http://localhost:8081/api/refresh | python3 -m json.tool
else
  # Not running -- refresh the cache directly.
  python3 scrape.py
fi
