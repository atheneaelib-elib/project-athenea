#!/usr/bin/env bash
# ==============================================================================
# Athenea - Automated Linux Installation Script
# https://github.com/atheneaelib-elib/project-athenea
#
# Supported Distributions:
#   - Debian 11 (Bullseye), Debian 12 (Bookworm)
#   - Ubuntu 20.04 (Focal), 22.04 (Jammy), 24.04 (Noble)
#   - AlmaLinux / Rocky Linux / RHEL 9+ (experimental)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/install.sh | sudo bash
# ==============================================================================

set -euo pipefail

# ANSI Color Codes
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${CYAN}${BOLD}"
cat << 'EOF'
     ___  _____ _   _ _____ _   _ _____ ___  
    / _ \/__   / / / / __  / \ | | ____/ _ \ 
   / /_\ \ / /\ / /_/ / /_/ /  \| |  _| / /_\ \
  / /   \// /  / __  / ____/ /\  | |___/ /   \ \
 /_/    \/_/  /_/ /_/_/   /_/  \_|_____/_/    \_/
       eBook & Digital Media Library Server
EOF
echo -e "${NC}"

info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    exit 1
}

# 1. Root verification
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root. Please run: sudo bash $0"
fi

# 2. Detect OS & Architecture
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "aarch64" ]; then
    error "Unsupported architecture: $ARCH. Athenea requires x86_64 (amd64) or aarch64 (arm64)."
fi

OS_FAMILY=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME=$ID
    OS_VERSION=$VERSION_ID
    if [[ "$ID" == "debian" || "$ID" == "ubuntu" || "$ID_LIKE" =~ (debian|ubuntu) ]]; then
        OS_FAMILY="debian"
    elif [[ "$ID" == "almalinux" || "$ID" == "rocky" || "$ID" == "rhel" || "$ID" == "fedora" || "$ID_LIKE" =~ (rhel|fedora) ]]; then
        OS_FAMILY="rhel"
    else
        warn "Unrecognized OS ID: $ID. Attempting standard Debian/Ubuntu installation..."
        OS_FAMILY="debian"
    fi
else
    error "/etc/os-release not found. Cannot determine Linux distribution."
fi

info "Detected operating system: ${PRETTY_NAME:-$ID} ($ARCH)"

# 3. Target Paths & User Setup
INSTALL_DIR="/opt/athenea"
APP_USER="athenea"
APP_GROUP="athenea"
ENV_FILE="/etc/athenea.env"
DATA_DIR="/data"
LIBRARY_DIR="/data/books"
CACHE_DIR="/data/cache/thumbs"
BACKUP_DIR="/data/backups"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
if [ -n "$GITHUB_TOKEN" ]; then
    REPO_URL="https://${GITHUB_TOKEN}@github.com/atheneaelib-elib/project-athenea.git"
else
    REPO_URL="https://github.com/atheneaelib-elib/project-athenea.git"
fi

# 4. Install Prerequisites
info "Updating system package repositories..."
if [ "$OS_FAMILY" == "debian" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    info "Installing system dependencies..."
    apt-get install -y \
        ca-certificates \
        curl \
        wget \
        git \
        rsync \
        pigz \
        build-essential \
        calibre \
        ffmpeg \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        postgresql \
        postgresql-contrib \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        libffi-dev
elif [ "$OS_FAMILY" == "rhel" ]; then
    dnf -y update
    dnf -y install \
        ca-certificates \
        curl \
        wget \
        git \
        rsync \
        pigz \
        gcc \
        python3 \
        python3-devel \
        python3-pip \
        postgresql-server \
        postgresql-contrib \
        libpq-devel \
        libjpeg-turbo-devel \
        zlib-devel
    if ! postgresql-setup --status &>/dev/null; then
        postgresql-setup --initdb || true
    fi
fi

# Ensure PostgreSQL service is enabled and started
info "Starting and enabling PostgreSQL service..."
systemctl enable --now postgresql
sleep 1

# 5. Database & User Provisioning
DB_NAME="athenea"
DB_USER="athenea"
DB_PASS=$(openssl rand -hex 16)

info "Configuring PostgreSQL user and database..."
# Check if database user already exists
USER_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" || echo "0")
if [ "$USER_EXISTS" != "1" ]; then
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH ENCRYPTED PASSWORD '$DB_PASS' SUPERUSER;"
    success "Created PostgreSQL user: $DB_USER"
else
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH SUPERUSER;"
    info "PostgreSQL user '$DB_USER' already exists; granted superuser."
fi

# Check if database already exists
DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" || echo "0")
if [ "$DB_EXISTS" != "1" ]; then
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
    success "Created PostgreSQL database: $DB_NAME"
fi

sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" || true
sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS unaccent;" || true
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA public TO $DB_USER;" || true

# 6. Create Dedicated System User
if ! id "$APP_USER" &>/dev/null; then
    info "Creating system user '$APP_USER'..."
    useradd --system --shell /bin/bash --home-dir "$INSTALL_DIR" --no-create-home "$APP_USER"
    success "Created system user: $APP_USER"
else
    info "System user '$APP_USER' already exists."
fi

# 7. Provision Storage Directories
info "Setting up storage directories..."
mkdir -p "$LIBRARY_DIR" "$CACHE_DIR" "$BACKUP_DIR"
chown -R "$APP_USER:$APP_GROUP" "$DATA_DIR"
chmod 775 "$LIBRARY_DIR" "$CACHE_DIR" "$BACKUP_DIR"
success "Storage paths ready at $DATA_DIR"

# 8. Clone or Update Application Repository
if [ ! -d "$INSTALL_DIR/.git" ]; then
    info "Cloning Athenea repository to $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR" && git remote set-url origin https://github.com/atheneaelib-elib/project-athenea.git
    chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"
    success "Cloned Athenea repository."
else
    info "Athenea repository found at $INSTALL_DIR. Pulling latest updates..."
    cd "$INSTALL_DIR"
    git fetch origin
    git reset --hard origin/main
    chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"
    success "Repository updated."
fi

# 9. Setup Python Virtual Environment
VENV_PATH="$INSTALL_DIR/venv"
info "Setting up Python virtual environment in $VENV_PATH..."
if [ ! -d "$VENV_PATH" ]; then
    sudo -u "$APP_USER" python3 -m venv "$VENV_PATH"
fi

info "Installing Python dependencies (fastapi, sqlalchemy, psycopg, edge-tts, etc.)..."
sudo -u "$APP_USER" "$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel
sudo -u "$APP_USER" "$VENV_PATH/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
success "Python virtual environment and dependencies installed."

info "Downloading high-fidelity Russian Piper neural voice models..."
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
success "Russian Piper neural TTS voices installed."

# 10. Generate /etc/athenea.env
if [ ! -f "$ENV_FILE" ]; then
    info "Creating environment configuration at $ENV_FILE..."
    cat << EOF > "$ENV_FILE"
# ===================================================================
# Athenea Environment Configuration
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

# Registration
ALLOW_REGISTRATION=false
REQUIRE_APPROVAL=true
EOF
    chmod 600 "$ENV_FILE"
    chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
    success "Created $ENV_FILE"
else
    info "Existing configuration found at $ENV_FILE; leaving intact."
fi

# 11. Configure Systemd Service
SERVICE_FILE="/etc/systemd/system/athenea.service"
info "Installing systemd service to $SERVICE_FILE..."
cp "$INSTALL_DIR/deploy/athenea.service" "$SERVICE_FILE"
chmod 644 "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable athenea.service
systemctl restart athenea.service
success "Athenea service enabled and started."

# 12. Verification & Completion
info "Waiting for service startup..."
sleep 3

SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo -e "\n${GREEN}${BOLD}==============================================================================${NC}"
echo -e "${GREEN}${BOLD}   🎉 Athenea Installation Successfully Completed! 🎉${NC}"
echo -e "${GREEN}${BOLD}==============================================================================${NC}\n"
echo -e "Access the First-Run Web Setup Wizard to initialize your admin account:"
echo -e "👉 ${CYAN}${BOLD}http://${SERVER_IP}:8000/setup${NC} (or http://localhost:8000/setup)\n"
echo -e "${BOLD}Server Details:${NC}"
echo -e " • Application Directory: ${CYAN}$INSTALL_DIR${NC}"
echo -e " • Environment File:     ${CYAN}$ENV_FILE${NC}"
echo -e " • Library Book Storage:  ${CYAN}$LIBRARY_DIR${NC}"
echo -e " • Database:              ${CYAN}PostgreSQL ($DB_NAME on localhost)${NC}"
echo -e " • System Service:        ${CYAN}systemctl status athenea${NC}"
echo -e "\nEnjoy reading with Athenea!\n"
