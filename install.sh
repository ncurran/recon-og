#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="/usr/local/bin"

install_wrapper() {
    local src="$1"
    local dest="$BIN_DIR/$2"
    cat > "$dest" <<EOF
#!/bin/bash
cd "$REPO_DIR" && ./$src "\$@"
EOF
    chmod +x "$dest"
    echo "  installed $dest"
}

if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo: sudo bash install.sh"
    exit 1
fi

echo "Installing recon-og from $REPO_DIR"
install_wrapper recon-og  recon-og
install_wrapper recon-cli recon-cli
install_wrapper recon-web recon-web
echo "Done. Run: recon-og"
