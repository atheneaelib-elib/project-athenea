#!/usr/bin/env bash
# ==============================================================================
# Athenea - Container Provisioning Script (Executed inside Debian 12 LXC)
# https://github.com/atheneaelib-elib/project-athenea
# ==============================================================================

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

msg_info() { echo -e "${CYAN}⚡ [INFO]${NC} $1"; }
msg_ok() { echo -e "${GREEN}✔ [OK]${NC} $1"; }
msg_warn() { echo -e "${YELLOW}⚠ [WARN]${NC} $1"; }
msg_err() { echo -e "${RED}✖ [ERROR]${NC} $1" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive

msg_info "Updating container packages..."
apt-get update -y
apt-get upgrade -y
msg_ok "Container system up to date."

msg_info "Installing core dependencies and build utilities..."
apt-get install -y \
    ca-certificates \
    curl \
    wget \
    git \
    openssh-server \
    rsync \
    pigz \
    sudo \
    build-essential \
    calibre \
    ffmpeg \
    p7zip-full \
    libarchive-tools \
    djvulibre-bin \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    postgresql \
    postgresql-contrib \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    libffi-dev \
    tzdata
msg_ok "Prerequisites installed."

msg_info "Starting and enabling PostgreSQL database server..."
systemctl enable --now postgresql
sleep 1

# Database and system user
APP_USER="athenea"
APP_GROUP="athenea"
INSTALL_DIR="/opt/athenea"
DATA_DIR="/data"
LIBRARY_DIR="/data/books"
CACHE_DIR="/data/cache/thumbs"
BACKUP_DIR="/data/backups"
ENV_FILE="/etc/athenea.env"
DB_NAME="athenea"
DB_USER="athenea"
DB_PASS=$(openssl rand -hex 16)

msg_info "Configuring PostgreSQL user and database..."
USER_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" || echo "0")
if [ "$USER_EXISTS" != "1" ]; then
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH ENCRYPTED PASSWORD '$DB_PASS' SUPERUSER;"
    msg_ok "Created database user: $DB_USER"
else
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH SUPERUSER;"
fi

DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" || echo "0")
if [ "$DB_EXISTS" != "1" ]; then
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;" || \
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
    msg_ok "Created database: $DB_NAME (UTF-8)"
fi

sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" || true
sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS unaccent;" || true
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA public TO $DB_USER;" || true

msg_info "Creating system user '$APP_USER'..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --shell /bin/bash --home-dir "$INSTALL_DIR" --no-create-home "$APP_USER"
    msg_ok "Created user $APP_USER"
fi

msg_info "Creating library storage directories..."
mkdir -p "$LIBRARY_DIR" "$CACHE_DIR" "$BACKUP_DIR"
chown -R "$APP_USER:$APP_GROUP" "$DATA_DIR"
chmod 775 "$LIBRARY_DIR" "$CACHE_DIR" "$BACKUP_DIR"
msg_ok "Storage directories provisioned at $DATA_DIR"

msg_info "Cloning Athenea repository..."
git config --global --add safe.directory "$INSTALL_DIR" || true
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
if [ -n "$GITHUB_TOKEN" ]; then
    CLONE_URL="https://${GITHUB_TOKEN}@github.com/atheneaelib-elib/project-athenea.git"
else
    CLONE_URL="https://github.com/atheneaelib-elib/project-athenea.git"
fi

if [ ! -d "$INSTALL_DIR/.git" ]; then
    rm -rf "$INSTALL_DIR"
    git clone "$CLONE_URL" "$INSTALL_DIR"
    # Reset origin URL so token is not left stored in config
    cd "$INSTALL_DIR"
    git remote set-url origin https://github.com/atheneaelib-elib/project-athenea.git
    chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"
    chmod +x "$INSTALL_DIR"/cli/*.sh 2>/dev/null || true
    msg_ok "Repository cloned to $INSTALL_DIR"
else
    cd "$INSTALL_DIR"
    git fetch origin
    git reset --hard origin/main
    chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"
    chmod +x "$INSTALL_DIR"/cli/*.sh 2>/dev/null || true
    msg_ok "Repository updated."
fi

msg_info "Setting up Python virtual environment..."
VENV_PATH="$INSTALL_DIR/venv"
if [ ! -d "$VENV_PATH" ]; then
    sudo -u "$APP_USER" python3 -m venv "$VENV_PATH"
fi

msg_info "Installing Python dependencies (Uvicorn, FastAPI, SQLAlchemy, Pillow, Edge-TTS, Piper)..."
sudo -u "$APP_USER" "$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel
sudo -u "$APP_USER" "$VENV_PATH/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
msg_ok "Virtual environment dependencies installed."

msg_info "Downloading high-fidelity Russian Piper neural voice models..."
mkdir -p "$INSTALL_DIR/models/piper"
mkdir -p "$INSTALL_DIR/cache/tts" "$INSTALL_DIR/cache/thumbs"
for v in denis ruslan irina; do
    if [ ! -f "$INSTALL_DIR/models/piper/ru_RU-${v}-medium.onnx" ]; then
        curl -sSL -o "$INSTALL_DIR/models/piper/ru_RU-${v}-medium.onnx" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/${v}/medium/ru_RU-${v}-medium.onnx"
        curl -sSL -o "$INSTALL_DIR/models/piper/ru_RU-${v}-medium.onnx.json" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/${v}/medium/ru_RU-${v}-medium.onnx.json"
    fi
done
# Map Dmitry to the resonant 132Hz Denis model so Dmitry is clearly male
ln -sf ru_RU-denis-medium.onnx "$INSTALL_DIR/models/piper/ru_dmitri.onnx"
ln -sf ru_RU-denis-medium.onnx.json "$INSTALL_DIR/models/piper/ru_dmitri.onnx.json"
ln -sf ru_RU-denis-medium.onnx "$INSTALL_DIR/models/piper/ru_dmitriy.onnx"
ln -sf ru_RU-denis-medium.onnx.json "$INSTALL_DIR/models/piper/ru_dmitriy.onnx.json"
ln -sf ru_RU-denis-medium.onnx "$INSTALL_DIR/models/piper/ru_RU-dmitriy-medium.onnx"
ln -sf ru_RU-denis-medium.onnx.json "$INSTALL_DIR/models/piper/ru_RU-dmitriy-medium.onnx.json"
ln -sf ru_RU-ruslan-medium.onnx "$INSTALL_DIR/models/piper/ru_ruslan.onnx"
ln -sf ru_RU-ruslan-medium.onnx.json "$INSTALL_DIR/models/piper/ru_ruslan.onnx.json"
ln -sf ru_RU-irina-medium.onnx "$INSTALL_DIR/models/piper/ru_irina.onnx"
ln -sf ru_RU-irina-medium.onnx.json "$INSTALL_DIR/models/piper/ru_irina.onnx.json"
ln -sf ru_RU-denis-medium.onnx "$INSTALL_DIR/models/piper/ru_RU-dmitri-medium.onnx"
ln -sf ru_RU-denis-medium.onnx.json "$INSTALL_DIR/models/piper/ru_RU-dmitri-medium.onnx.json"
chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR/models" "$INSTALL_DIR/cache"
msg_ok "Neural TTS voices installed."

msg_info "Writing environment configuration to $ENV_FILE..."
if [ ! -f "$ENV_FILE" ]; then
    cat << EOF > "$ENV_FILE"
# ===================================================================
# Athenea Environment Configuration (Proxmox LXC)
# ===================================================================

# Database Settings
DB_HOST=localhost
DB_PORT=5432
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS

# Storage Paths
LIBRARY_ROOT=$LIBRARY_DIR
BACKUP_DIR=$BACKUP_DIR
THUMB_CACHE_DIR=$CACHE_DIR

# Security Settings
COOKIE_SECURE=false
LOGIN_RATE_LIMIT_ATTEMPTS=5
LOGIN_RATE_LIMIT_WINDOW=300

# Registration Defaults
ALLOW_REGISTRATION=false
REQUIRE_APPROVAL=true
EOF
    chmod 600 "$ENV_FILE"
    chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
    msg_ok "Configuration file written."
fi

msg_info "Configuring Athenea systemd service..."
cp "$INSTALL_DIR/deploy/athenea.service" /etc/systemd/system/athenea.service
chmod 644 /etc/systemd/system/athenea.service

systemctl daemon-reload
systemctl enable athenea.service
systemctl restart athenea.service
msg_ok "Athenea service enabled and started."

msg_info "Cleaning up apt caches..."
apt-get autoremove -y
apt-get clean
rm -rf /var/lib/apt/lists/*
msg_ok "Cleanup completed."
