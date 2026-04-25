#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Help ───────────────────────────────────────
if [[ "${1:-}" == "--help" ]]; then
  cat << 'EOF'
Claude Code Installer - Deploy Script

Usage: ./deploy.sh [--test] [--help]

Options:
  --test    Deploy to test environment (default: production)
  --help    Show this help

Deploy uploads everything that changed (hash-based diff):
  - Server code (server.py, crypto.py, dashboard.html)
  - Data files (providers.json, version.json)
  - Crypto keys (first time only)
  - Release zip (if present)
  - Website files (index.html, assets)

Server service is restarted only when server code files actually changed.

Note: Run build.bat (Windows) to build exe and update config.json,
      then run deploy.sh to push everything.

EOF
  exit 0
fi

# ── Mode selection ──────────────────────────────
IS_TEST=false
for arg in "$@"; do
    case $arg in
        --test) IS_TEST=true ;;
    esac
done

MODE_LABEL="Prod"
$IS_TEST && MODE_LABEL="Test"

KEYS_DIR="$SCRIPT_DIR/server/keys"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if $IS_TEST; then
    TAG_COLOR='\033[1;33m'  # orange/yellow for test
else
    TAG_COLOR="$GREEN"
fi
TAG="[${MODE_LABEL}]"
info()  { echo -e "${TAG_COLOR}${TAG}${NC} $*"; }
warn()  { echo -e "${YELLOW}${TAG}${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Read config ─────────────────────────────────
CONFIG_FILE="$SCRIPTS_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    error "config.json not found. Run: cp scripts/config.example.json scripts/config.json  and fill in your server details."
fi

PYTHON=""
for cmd in python3 python; do
    command -v "$cmd" &>/dev/null && { PYTHON="$cmd"; break; }
done
[ -z "$PYTHON" ] && error "Python not found."

# SSH config
SSH_USER=$($PYTHON -c "import json; cfg=json.load(open('$CONFIG_FILE')); print(cfg.get('ssh',{}).get('user','root'))")
SSH_HOST=$($PYTHON -c "import json; cfg=json.load(open('$CONFIG_FILE')); print(cfg.get('ssh',{}).get('host',''))")
SSH_PORT=$($PYTHON -c "import json; cfg=json.load(open('$CONFIG_FILE')); print(cfg.get('ssh',{}).get('port','22'))")
SSH_KEY=$($PYTHON -c "import json; cfg=json.load(open('$CONFIG_FILE')); print(cfg.get('ssh',{}).get('key','$HOME/.ssh/id_rsa'))")

SSH_PORT="${SSH_PORT:-22}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_rsa}"
[ -z "$SSH_HOST" ] && error "ssh.host not set in config.json"

SSH_OPTS="-o StrictHostKeyChecking=accept-new"
[ -f "$SSH_KEY" ] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
ssh_cmd() { SSH_AUTH_SOCK= ALL_PROXY= http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= no_proxy='*' ssh $SSH_OPTS -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" "$@"; }
scp_cmd() { SSH_AUTH_SOCK= ALL_PROXY= http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= no_proxy='*' scp -O $SSH_OPTS -P "${SSH_PORT}" "$@"; }

# ── Hash helpers ──────────────────────────────────
local_md5() {
    if command -v md5sum &>/dev/null; then
        md5sum "$1" | awk '{print $1}'
    else
        md5 -q "$1"
    fi
}

_HASH_CACHE=""
_fetch_remote_hashes() {
    local script=""
    for f in "$@"; do
        script+="[ -f '$f' ] && md5sum '$f' || echo 'MISSING  $f';"
    done
    _HASH_CACHE=$(ssh_cmd "$script" 2>/dev/null) || _HASH_CACHE=""
}

_SERVER_CHANGED=false
upload_if_changed() {
    local local_file="$1"
    local remote_dest="$2"
    local remote_abs="$3"
    local name
    name="$(basename "$local_file")"

    local lhash rhash
    lhash="$(local_md5 "$local_file")"
    rhash=$(echo "$_HASH_CACHE" | grep " ${remote_abs}$" | awk '{print $1}')

    if [ -n "$rhash" ] && [ "$lhash" = "$rhash" ]; then
        info "  (unchanged) $name"
    else
        scp_cmd "$local_file" "$remote_dest"
        info "  (updated)   $name"
    fi
}

# Track if any server code file was actually uploaded
upload_server_file() {
    local local_file="$1"
    local remote_dest="$2"
    local remote_abs="$3"
    local name
    name="$(basename "$local_file")"

    local lhash rhash
    lhash="$(local_md5 "$local_file")"
    rhash=$(echo "$_HASH_CACHE" | grep " ${remote_abs}$" | awk '{print $1}')

    if [ -n "$rhash" ] && [ "$lhash" = "$rhash" ]; then
        info "  (unchanged) $name"
    else
        scp_cmd "$local_file" "$remote_dest"
        info "  (updated)   $name"
        _SERVER_CHANGED=true
    fi
}

get_port() {
    local mode="$1"
    $PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
print(cfg['servers']['$mode']['port'])
"
}

get_providers() {
    local mode="$1"
    $PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
print(json.dumps(cfg['providers']['$mode']))
"
}

if $IS_TEST; then
    REMOTE_SUBDIR="providers-api-test"
    DATA_FILE="providers.test.json"
    DOMAIN="api-test.claudecodeinstaller.com"
    SRV_PORT=$(get_port test)
    PROVIDERS_JSON=$(get_providers test)
else
    REMOTE_SUBDIR="providers-api"
    DATA_FILE="providers.json"
    DOMAIN="api.claudecodeinstaller.com"
    SRV_PORT=$(get_port prod)
    PROVIDERS_JSON=$(get_providers prod)
fi

echo "============================================"
echo "  Deploy ($MODE_LABEL)"
echo "============================================"
echo ""

# ── Extract data files ────────────────────────────
info "Extracting data..."
DATA_OUT_DIR="$SCRIPT_DIR/server/data"
mkdir -p "$DATA_OUT_DIR"
echo "$PROVIDERS_JSON" | $PYTHON -c "
import json, sys
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
out = {
    'info':            cfg.get('info', {}),
    'providers':       json.load(sys.stdin),
    'claude_settings': cfg.get('claude_settings', {}),
}
out_path = '$DATA_OUT_DIR/app_config.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print(f'  {out_path} updated.')
"
echo ""

# ── SSH connection ─────────────────────────────────
info "Testing SSH connection..."
ssh_cmd "echo 'SSH OK'" || error "Cannot connect."
REMOTE_HOME=$(ssh_cmd 'echo $HOME')
REMOTE_DIR="$REMOTE_HOME/$REMOTE_SUBDIR"
info "Remote: $REMOTE_DIR"
ssh_cmd "mkdir -p ${REMOTE_DIR}/data ${REMOTE_DIR}/keys"

# ── [1/4] Keys ─────────────────────────────────────
info "[1/4] Checking crypto keys..."
if [ ! -f "$KEYS_DIR/ed25519_private.pem" ]; then
    info "Generating keys..."
    mkdir -p "$KEYS_DIR" "$SCRIPT_DIR/app/keys"
    $PYTHON -c "
import base64, json
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

keys_dir = Path('$KEYS_DIR')
client_dir = Path('$SCRIPT_DIR/app/keys')

aes_key = AESGCM.generate_key(bit_length=256)
(keys_dir / 'aes.key').write_bytes(aes_key)

private_key = Ed25519PrivateKey.generate()
(keys_dir / 'ed25519_private.pem').write_bytes(
    private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
public_key = private_key.public_key()
(keys_dir / 'ed25519_public.pem').write_bytes(
    public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))

public_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
(client_dir / 'public.json').write_text(json.dumps({
    'aes_key': base64.b64encode(aes_key).decode(),
    'ed_public_key': base64.b64encode(public_bytes).decode(),
}, indent=2), encoding='utf-8')

print(f'Keys generated in {keys_dir}')
"
    info "Keys generated."
else
    info "Keys already exist, reusing."
fi

if [ -d "$KEYS_DIR" ] && ls "$KEYS_DIR"/*.{key,pem} &>/dev/null; then
    info "Syncing crypto keys (hash-based diff)..."
    _KEY_FILES=()
    for kf in "$KEYS_DIR"/*.key "$KEYS_DIR"/*.pem; do
        [ -f "$kf" ] && _KEY_FILES+=("${REMOTE_DIR}/keys/$(basename "$kf")")
    done
    _fetch_remote_hashes "${_KEY_FILES[@]}"
    _KEY_CHANGED=false
    for kf in "$KEYS_DIR"/*.key "$KEYS_DIR"/*.pem; do
        [ -f "$kf" ] || continue
        remote_abs="${REMOTE_DIR}/keys/$(basename "$kf")"
        lhash="$(local_md5 "$kf")"
        rhash=$(echo "$_HASH_CACHE" | grep " ${remote_abs}$" | awk '{print $1}')
        if [ -z "$rhash" ] || [ "$lhash" != "$rhash" ]; then
            scp_cmd "$kf" "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/keys/"
            info "  (updated)   $(basename "$kf")"
            _KEY_CHANGED=true
        else
            info "  (unchanged) $(basename "$kf")"
        fi
    done
    if $_KEY_CHANGED; then
        warn "Crypto keys updated — existing encrypted data on server will need re-encryption."
        # Force old clients (built with old AES key) to upgrade by bumping min_version
        _CUR_VER=$($PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
print(cfg.get('info', {}).get('latest', {}).get('version', ''))
" 2>/dev/null)
        if [ -n "$_CUR_VER" ]; then
            $PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
cfg.setdefault('info', {})['min_version'] = '$_CUR_VER'
with open('$CONFIG_FILE', 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print('  config.json min_version -> $_CUR_VER')
"
            warn "min_version bumped to $_CUR_VER — old clients will be forced to update."
        fi
    fi
fi
echo ""

# ── [2/4] Upload server files ──────────────────────
info "[2/4] Uploading server files..."
_fetch_remote_hashes \
    "${REMOTE_DIR}/server.py" \
    "${REMOTE_DIR}/crypto.py" \
    "${REMOTE_DIR}/dashboard.html" \
    "${REMOTE_DIR}/data/app_config.json"
upload_server_file "$SCRIPT_DIR/server/server.py"      "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/server.py"      "${REMOTE_DIR}/server.py"
upload_server_file "$SCRIPT_DIR/server/crypto.py"      "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/crypto.py"      "${REMOTE_DIR}/crypto.py"
upload_server_file "$SCRIPT_DIR/server/dashboard.html" "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/dashboard.html" "${REMOTE_DIR}/dashboard.html"
# Sync dashboard.html to nginx static dir (nginx runs as www-data, cannot follow symlinks into /root)
if $IS_TEST; then
    ssh_cmd "cp ${REMOTE_DIR}/dashboard.html /var/www/cc-dashboard-test/dashboard.html"
else
    ssh_cmd "cp ${REMOTE_DIR}/dashboard.html /var/www/cc-dashboard/dashboard.html"
fi
upload_if_changed  "$SCRIPT_DIR/server/data/app_config.json" "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/data/app_config.json" "${REMOTE_DIR}/data/app_config.json"

info "Cleaning up legacy data files..."
ssh_cmd "
    for f in providers.json providers.test.json version.json info.json claude_settings.json; do
        [ -f ${REMOTE_DIR}/data/\$f ] && rm ${REMOTE_DIR}/data/\$f && echo \"  removed: \$f\" || true
    done
"

info "Writing remote config..."
ssh_cmd "cat > ${REMOTE_DIR}/.env << 'ENVEOF'
HOST=0.0.0.0
PORT=${SRV_PORT}
ENVEOF"

info "Installing remote dependencies..."
ssh_cmd "
    if [ ! -f ${REMOTE_DIR}/venv/bin/pip ]; then
        rm -rf ${REMOTE_DIR}/venv
        python3 -m venv ${REMOTE_DIR}/venv --without-pip
        curl -sS https://bootstrap.pypa.io/get-pip.py | ${REMOTE_DIR}/venv/bin/python3
    fi
    ${REMOTE_DIR}/venv/bin/pip install cryptography --quiet 2>/dev/null
"
echo ""

# ── [2.5/4] Release (auto-detect: exe in dist/) ───
EXE_PATH="$SCRIPTS_DIR/dist/ClaudeCodeInstaller.exe"
if [ -f "$EXE_PATH" ]; then
    APP_VER=$($PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
print(cfg.get('info', {}).get('latest', {}).get('version', ''))
" 2>/dev/null)

    if [ -n "$APP_VER" ]; then
        info "Packaging release v${APP_VER}..."
        _PKG_OUTPUT=$($PYTHON "$SCRIPTS_DIR/_build_helpers.py" package "$APP_VER" 2>&1) || error "Package failed."
        echo "$_PKG_OUTPUT"
        if echo "$_PKG_OUTPUT" | grep -q "UNCHANGED"; then
            info "  Release unchanged, skipping upload."
        else
            ZIP_PATH="$SCRIPT_DIR/www/assets/release/ClaudeCodeInstaller.zip"
            if [ -f "$ZIP_PATH" ]; then
                scp_cmd "$ZIP_PATH" "${SSH_USER}@${SSH_HOST}:/var/www/cc/assets/release/"
                info "Uploaded: https://claudecodeinstaller.com/assets/release/ClaudeCodeInstaller.zip"
            fi
        fi
    else
        warn "exe found but info.latest.version not set in config.json, skipping release."
    fi
    echo ""
fi

# ── [2.5/4] Website files (prod only) ───────────────
if [ -f "$SCRIPT_DIR/www/index.html" ] && ! $IS_TEST; then
    info "Uploading website..."

    # Inject version into a temp copy of index.html
    _APP_VER=$($PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
print(cfg.get('info', {}).get('latest', {}).get('version', ''))
")
    _INDEX_TMP="$SCRIPT_DIR/www/_index_tmp.html"
    sed "s/__APP_VERSION__/${_APP_VER}/g" "$SCRIPT_DIR/www/index.html" > "$_INDEX_TMP"

    _WEBSITE_FILES=()
    _WEBSITE_FILES+=("/var/www/cc/index.html")
    for f in "$SCRIPT_DIR/www/assets/"*.png "$SCRIPT_DIR/www/assets/"*.mp4; do
        [ -f "$f" ] && _WEBSITE_FILES+=("/var/www/cc/assets/$(basename "$f")")
    done
    _fetch_remote_hashes "${_WEBSITE_FILES[@]}"
    upload_if_changed \
        "$_INDEX_TMP" \
        "${SSH_USER}@${SSH_HOST}:/var/www/cc/index.html" \
        "/var/www/cc/index.html"
    rm -f "$_INDEX_TMP"
    for f in "$SCRIPT_DIR/www/assets/"*.png "$SCRIPT_DIR/www/assets/"*.mp4; do
        [ -f "$f" ] && upload_if_changed \
            "$f" \
            "${SSH_USER}@${SSH_HOST}:/var/www/cc/assets/" \
            "/var/www/cc/assets/$(basename "$f")"
    done
    info "Website: https://claudecodeinstaller.com"
    echo ""
fi

# ── [3/4] Service ──────────────────────────────────
SERVICE_NAME="${REMOTE_SUBDIR}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
info "[3/4] Service (${SERVICE_NAME})..."

ssh_cmd "cat > ${SERVICE_FILE} << 'SVCEOF'
[Unit]
Description=Claude Code Installer Providers API (${MODE_LABEL})
After=network.target

[Service]
Type=simple
WorkingDirectory=${REMOTE_DIR}
ExecStart=${REMOTE_DIR}/venv/bin/python3 ${REMOTE_DIR}/server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}"

if $_SERVER_CHANGED; then
    info "Server code changed, restarting service..."
    ssh_cmd "systemctl restart ${SERVICE_NAME}
sleep 2
systemctl is-active --quiet ${SERVICE_NAME} \
    && echo 'Service started successfully' \
    || { echo 'Service failed to start:'; journalctl -u ${SERVICE_NAME} -n 20 --no-pager; exit 1; }"
else
    info "Server code unchanged, no restart needed."
fi
echo ""

# ── [4/4] Verify ───────────────────────────────────
info "[4/4] Verifying..."
HTTP_CODE=$(ssh_cmd "curl -s -o /dev/null -w '%{http_code}' http://localhost:${SRV_PORT}/config.json" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    info "Server responding (HTTP 200)"
else
    warn "Server returned HTTP $HTTP_CODE"
fi
echo ""

echo "============================================"
echo "  $MODE_LABEL deployment complete!"
echo ""
echo "  API:        https://$DOMAIN/config.json"
echo "  Dashboard:  https://claudecodeinstaller.com/dashboard$([ "$IS_TEST" = true ] && echo '-test' || echo '')"
echo "  Logs:       ssh ${SSH_USER}@${SSH_HOST} 'journalctl -u ${SERVICE_NAME} -f'"
echo "  Status:     ssh ${SSH_USER}@${SSH_HOST} 'systemctl status ${SERVICE_NAME}'"
echo "============================================"
