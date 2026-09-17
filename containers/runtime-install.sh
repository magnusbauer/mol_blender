#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive
cat > /etc/apt/sources.list.d/debian.sources <<'SOURCES'
Types: deb deb-src
URIs: https://snapshot.debian.org/archive/debian/20260906T000000Z/
Suites: trixie trixie-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
Check-Valid-Until: no

Types: deb deb-src
URIs: https://snapshot.debian.org/archive/debian-security/20260906T000000Z/
Suites: trixie-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
Check-Valid-Until: no
SOURCES
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl xz-utils python3-pymol ffmpeg \
  libgl1 libegl1 libx11-6 libxi6 libxrender1 libxfixes3 libxxf86vm1 \
  libxkbcommon0 libsm6 libice6 libglib2.0-0 libfontconfig1 libxrandr2 \
  libxinerama1 libxcursor1 libwayland-client0 libpulse0
mkdir -p /opt/notices
dpkg-query -W -f='${Package}\t${Version}\t${source:Package}\t${source:Version}\n' > /opt/notices/debian-packages.tsv
cp /opt/LICENSE /opt/THIRD_PARTY_NOTICES.md /opt/notices/
cp /opt/blender_render/GPL-3.0-or-later.txt /opt/notices/
python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /opt/blender_render/service-requirements.txt
PYTHONPATH=/opt python -c 'from blender_render.runtime_install import install; install(root="/opt/runtime")'
ln -s /opt/runtime/blender-5.0.1-linux-x64/blender /usr/local/bin/blender
rm -rf /var/lib/apt/lists/*
