#!/usr/bin/env bash
# Tarik versi terbaru dari repo lalu nyalakan ulang layanannya.
# config.json tidak pernah ikut ditarik -- dia terkecuali di .gitignore.
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"
if systemctl list-unit-files ruri.service >/dev/null 2>&1; then
  $SUDO systemctl restart ruri && sleep 4 && systemctl is-active ruri
else
  echo "unit systemd 'ruri' nggak ada -- jalankan ./run.sh sendiri"
fi
