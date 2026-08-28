#!/bin/bash
set -e

# Artsite Quadlet Deployment Script
# This script installs the quadlet files for rootless podman

QUADLET_DIR="${HOME}/.config/containers/systemd"
CONFIG_DIR="${HOME}/.config/artsite"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEDIA_DIR="/srv/artsite/media"  # host path bind-mounted at /data/media (point at your NAS)
PRIVATE_DIR="/srv/artsite/private"  # staff-only documents, bind-mounted at /data/private (NOT served by Caddy)

echo "==> Creating directories..."
mkdir -p "${QUADLET_DIR}"
mkdir -p "${CONFIG_DIR}"

echo "==> Copying quadlet files to ${QUADLET_DIR}..."
cp "${SCRIPT_DIR}/quadlet/"*.network "${QUADLET_DIR}/"
cp "${SCRIPT_DIR}/quadlet/"*.volume "${QUADLET_DIR}/"
cp "${SCRIPT_DIR}/quadlet/"*.container "${QUADLET_DIR}/"

echo "==> Setting permissions..."
chmod 644 "${QUADLET_DIR}"/artsite*

# Check if environment file exists
if [ ! -f "${CONFIG_DIR}/artsite.env" ]; then
    echo ""
    echo "WARNING: Environment file not found at ${CONFIG_DIR}/artsite.env"
    echo "Please copy deploy/artsite.env.example to ${CONFIG_DIR}/artsite.env"
    echo "and fill in the required values before starting the services."
    echo ""
fi

# Check the media directory exists (the local storage backend writes uploads
# here; the quadlet bind-mounts it at /data/media). It must be writable by this
# user and readable by the host's Caddy — see deploy/SELF_HOSTING.md.
if [ ! -d "${MEDIA_DIR}" ]; then
    echo ""
    echo "WARNING: Media directory not found at ${MEDIA_DIR}"
    echo "Create it (point it at your NAS mount) before starting the services, e.g.:"
    echo "  sudo mkdir -p ${MEDIA_DIR} && sudo chown \$USER ${MEDIA_DIR}"
    echo "See deploy/SELF_HOSTING.md for the rootless podman permission notes."
    echo ""
fi

# Check the private documents directory exists (staff-only files; bind-mounted
# at /data/private). It must NOT be served by Caddy — keep it off the media tree.
if [ ! -d "${PRIVATE_DIR}" ]; then
    echo ""
    echo "WARNING: Private documents directory not found at ${PRIVATE_DIR}"
    echo "Create it before starting the services, e.g.:"
    echo "  sudo mkdir -p ${PRIVATE_DIR} && sudo chown \$USER ${PRIVATE_DIR}"
    echo "Do NOT add a Caddy route for it — it's served only by the gated download view."
    echo ""
fi

echo "==> Reloading systemd user daemon..."
systemctl --user daemon-reload

echo "==> Quadlet files installed successfully!"
echo ""
echo "Next steps:"
echo "  1. Ensure ${CONFIG_DIR}/artsite.env exists with proper values"
echo "  2. Ensure the media directory ${MEDIA_DIR} exists and is writable"
echo "  3. Build the container image: podman build -t artsite ."
echo "  4. Start the services: systemctl --user start artsite.service"
echo "  5. Check status: systemctl --user status artsite.service"
echo "  6. View logs: journalctl --user -u artsite.service -f"
echo ""
# Quadlet-generated units can't be enabled by hand; they carry
# [Install] WantedBy=default.target and start with the user's systemd instance.
echo "Auto-start on boot only needs linger (the units are auto-enabled):"
echo "  sudo loginctl enable-linger \$USER"
