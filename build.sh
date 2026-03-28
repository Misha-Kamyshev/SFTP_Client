#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_NAME="sftp-sync-client"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-amd64}"

DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build"
PYINSTALLER_WORK_DIR="$BUILD_DIR/pyinstaller"
PYINSTALLER_SPEC_DIR="$BUILD_DIR/pyinstaller-spec"
DIST_APP_DIR="$DIST_DIR/$PACKAGE_NAME"
PKG_DIR="$ROOT_DIR/pkg"
DEBIAN_DIR="$PKG_DIR/DEBIAN"
OPT_DIR="$PKG_DIR/opt/$PACKAGE_NAME"
BIN_LINK_PATH="$PKG_DIR/usr/bin/$PACKAGE_NAME"
APPLICATIONS_DIR="$PKG_DIR/usr/share/applications"
ICONS_DIR="$PKG_DIR/usr/share/icons/hicolor/256x256/apps"
OUTPUT_DEB="$DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

ICON_CANDIDATES=(
    "$ROOT_DIR/assets/icons/$PACKAGE_NAME.png"
    "$ROOT_DIR/assets/$PACKAGE_NAME.png"
    "$ROOT_DIR/assets/icon.png"
    "$ROOT_DIR/resources/$PACKAGE_NAME.png"
    "$ROOT_DIR/resources/icon.png"
)

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command not found: $1" >&2
        exit 1
    fi
}

find_icon() {
    local candidate
    for candidate in "${ICON_CANDIDATES[@]}"; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

build_runtime_depends() {
    local -a elf_files=()
    local library_search_path
    local shlibdeps_output

    while IFS= read -r -d '' candidate; do
        if file -b "$candidate" | grep -q '^ELF '; then
            elf_files+=("$candidate")
        fi
    done < <(find "$OPT_DIR" -type f -print0)

    if [[ ${#elf_files[@]} -eq 0 ]]; then
        return 0
    fi

    library_search_path="$(find "$OPT_DIR" -type d | paste -sd: -)"
    shlibdeps_output="$(dpkg-shlibdeps -O --ignore-missing-info -l"$library_search_path" "${elf_files[@]}")"
    sed -n 's/^shlibs:Depends=//p' <<<"$shlibdeps_output"
}

write_control_file() {
    local package_depends="$1"

    cat > "$DEBIAN_DIR/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: LocalTools
EOF

    if [[ -n "$package_depends" ]]; then
        printf 'Depends: %s\n' "$package_depends" >> "$DEBIAN_DIR/control"
    else
        printf 'Depends: libglib2.0-0, libxkbcommon0, libegl1, libdbus-1-3\n' >> "$DEBIAN_DIR/control"
    fi

    cat >> "$DEBIAN_DIR/control" <<'EOF'
Description: SFTP Sync Client
EOF
}

write_desktop_file() {
    cat > "$APPLICATIONS_DIR/$PACKAGE_NAME.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=SFTP Sync Client
Exec=/opt/sftp-sync-client/sftp-sync-client
Icon=sftp-sync-client
Terminal=false
Categories=Network;Utility;
EOF
}

run_binary_check() {
    local binary_path="$1"

    if ! command -v timeout >/dev/null 2>&1; then
        echo "Skipping runtime smoke test: timeout is not available."
        return 0
    fi

    local exit_code=0
    set +e
    env QT_QPA_PLATFORM=offscreen XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}" \
        timeout 5s "$binary_path" >/dev/null 2>&1
    exit_code=$?
    set -e

    if [[ $exit_code -eq 0 || $exit_code -eq 124 ]]; then
        echo "Runtime smoke test passed for $binary_path"
        return 0
    fi

    echo "Runtime smoke test failed for $binary_path with exit code $exit_code" >&2
    exit "$exit_code"
}

require_command python3
require_command dpkg-deb
require_command dpkg-shlibdeps
require_command file

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
    echo "PyInstaller is required. Install it with: python3 -m pip install '.[build]'" >&2
    exit 1
fi

rm -rf "$DIST_APP_DIR" "$PYINSTALLER_WORK_DIR" "$PYINSTALLER_SPEC_DIR" "$PKG_DIR"
mkdir -p "$DIST_DIR" "$PYINSTALLER_WORK_DIR" "$PYINSTALLER_SPEC_DIR"
mkdir -p "$DEBIAN_DIR" "$OPT_DIR" "$(dirname "$BIN_LINK_PATH")" "$APPLICATIONS_DIR"

python3 -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "$PACKAGE_NAME" \
    --add-data "$ROOT_DIR/assets:assets" \
    --distpath "$DIST_DIR" \
    --workpath "$PYINSTALLER_WORK_DIR" \
    --specpath "$PYINSTALLER_SPEC_DIR" \
    "$ROOT_DIR/main.py"

if [[ ! -x "$DIST_APP_DIR/$PACKAGE_NAME" ]]; then
    echo "PyInstaller output not found at $DIST_APP_DIR/$PACKAGE_NAME" >&2
    exit 1
fi

cp -r "$DIST_APP_DIR"/. "$OPT_DIR/"
chmod +x "$OPT_DIR/$PACKAGE_NAME"
find "$OPT_DIR" -type f -name '*.so' -exec chmod +x {} +
ln -s "/opt/$PACKAGE_NAME/$PACKAGE_NAME" "$BIN_LINK_PATH"

write_control_file "$(build_runtime_depends || true)"
write_desktop_file

if icon_path="$(find_icon)"; then
    mkdir -p "$ICONS_DIR"
    cp "$icon_path" "$ICONS_DIR/$PACKAGE_NAME.png"
fi

chmod 755 "$APPLICATIONS_DIR/$PACKAGE_NAME.desktop"
chmod 644 "$DEBIAN_DIR/control"

dpkg-deb --build "$PKG_DIR" "$OUTPUT_DEB"

echo
echo "Verification:"
file "$OPT_DIR/$PACKAGE_NAME"
dpkg-deb --contents "$OUTPUT_DEB"
run_binary_check "$DIST_APP_DIR/$PACKAGE_NAME"

echo
echo "Built package: $OUTPUT_DEB"
