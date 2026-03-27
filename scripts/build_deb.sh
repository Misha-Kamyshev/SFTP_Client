#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/build/deb"
STAGE_DIR="$BUILD_DIR/stage"
APP_DIR="$STAGE_DIR/opt/sftp-sync-client"
VENV_DIR="$APP_DIR/.venv"
PACKAGE_NAME="sftp-sync-client"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-all}"
OUTPUT_FILE="$ROOT_DIR/dist/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

rm -rf "$BUILD_DIR"
mkdir -p "$APP_DIR" "$STAGE_DIR/DEBIAN" "$STAGE_DIR/usr/bin" "$STAGE_DIR/usr/share/applications" "$ROOT_DIR/dist"

cp -r "$ROOT_DIR/app" "$APP_DIR/app"
cp "$ROOT_DIR/main.py" "$ROOT_DIR/README.md" "$ROOT_DIR/requirements.txt" "$ROOT_DIR/pyproject.toml" "$APP_DIR/"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

cat > "$STAGE_DIR/usr/bin/sftp-sync-client" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /opt/sftp-sync-client/.venv/bin/python /opt/sftp-sync-client/main.py "$@"
EOF
chmod 755 "$STAGE_DIR/usr/bin/sftp-sync-client"

cat > "$STAGE_DIR/usr/share/applications/sftp-sync-client.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=SFTP Sync Client
Exec=/usr/bin/sftp-sync-client
Terminal=false
Categories=Network;Utility;
EOF

INSTALLED_SIZE="$(du -sk "$STAGE_DIR" | cut -f1)"
cat > "$STAGE_DIR/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: LocalTools
Depends: python3
Installed-Size: $INSTALLED_SIZE
Description: Desktop SFTP sync client built with PySide6 and Paramiko.
EOF

dpkg-deb --build "$STAGE_DIR" "$OUTPUT_FILE"
echo "Built $OUTPUT_FILE"
