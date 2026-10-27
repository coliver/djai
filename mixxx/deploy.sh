#!/usr/bin/env bash
# Copy the DJAI controller mappings into Mixxx's Windows user controllers folder.
# Mixxx reads mappings at startup (or when re-selected in Preferences > Controllers).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dest="$(python3 "$here/../tools/config.py" mixxx_user_dir)/controllers"

cp -v "$here"/controllers/* "$dest"/
