#!/usr/bin/env bash
# Deprecated: old deployment reintroduced an unstyled feature section.
# Retained as a safe compatibility wrapper only.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/repair_public_site.sh"
