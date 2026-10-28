#!/usr/bin/env bash
# ==============================================================================
# Athenea - Proxmox VE Helper Script (LXC Container Creator)
# https://github.com/atheneaelib-elib/project-athenea
#
# Run this script in the Proxmox VE Node Shell:
#   bash -c "$(wget -qLO - https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/ct/athenea.sh)"
# ==============================================================================

set -euo pipefail

# ANSI Color Codes
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

# Title & Brand
APP_NAME="Athenea"
APP_DESC="eBook & Digital Media Library Server"

echo -e "${CYAN}${BOLD}"
cat << 'EOF'
     ___  _____ _   _ _____ _   _ _____ ___  
    / _ \/__   / / / / __  / \ | | ____/ _ \ 
   / /_\ \ / /\ / /_/ / /_/ /  \| |  _| / /_\ \
  / /   \// /  / __  / ____/ /\  | |___/ /   \ \
 /_/    \/_/  /_/ /_/_/   /_/  \_|_____/_/    \_/
          Proxmox VE LXC Community Installer
EOF
echo -e "${NC}"

msg_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
msg_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
msg_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
msg_err() { echo -e "${RED}[ERROR]${NC} $1" >&2; exit 1; }

# 1. Environment Verification
if [ "$EUID" -ne 0 ]; then
    msg_err "This script must be run directly on the Proxmox VE host as root."
fi

if ! command -v pveversion >/dev/null 2>&1; then
    msg_err "This script requires Proxmox Virtual Environment (PVE). 'pveversion' not found."
fi

PVE_VER=$(pveversion | cut -d'/' -f2 | cut -d'-' -f1)
msg_info "Detected Proxmox VE version: $PVE_VER"

# Check internet connectivity
if ! ping -c 1 -W 2 1.1.1.1 &>/dev/null && ! ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
    msg_warn "Public DNS ping failed, testing HTTP reachability..."
    if ! curl -s --head https://raw.githubusercontent.com &>/dev/null; then
        msg_err "No active internet connection detected on this Proxmox VE host."
    fi
fi

# Ensure whiptail is available
if ! command -v whiptail >/dev/null 2>&1; then
    apt-get update && apt-get install -y whiptail
fi

# 2. Welcome & Confirmation Modal
whiptail --backtitle "Proxmox VE - Athenea Installer" \
    --title "Install Athenea LXC Container" \
    --yesno "This script will create and configure a high-performance Debian 12 LXC Container for Athenea eBook Library.\n\nWould you like to proceed?" 10 68 || exit 0

# 3. Settings Profile (Default vs Advanced)
SETTINGS_MODE=$(whiptail --backtitle "Proxmox VE - Athenea Installer" \
    --title "Configuration Mode" \
    --radiolist "Choose your installation settings profile:" 11 68 2 \
    "Default" "2 Cores, 2GB RAM, 16GB Disk, DHCP (Recommended)" ON \
    "Advanced" "Customize CT ID, Hostname, CPU, RAM, Disk, Storage & IP" OFF \
    3>&1 1>&2 2>&3) || exit 0

# Find next available CT ID
NEXT_CT_ID=$(pvesh get /cluster/nextid)

# Detect default container storage pool (look for local-lvm, local-zfs, then first storage supporting rootdir)
DEFAULT_STORAGE=""
for s in "local-lvm" "local-zfs" "local"; do
    if pvesm status -storage "$s" &>/dev/null; then
        DEFAULT_STORAGE="$s"
        break
    fi
done

if [ -z "$DEFAULT_STORAGE" ]; then
    DEFAULT_STORAGE=$(pvesm status -content rootdir | awk 'NR>1 {print $1}' | head -n 1)
fi

# Detect template storage pool (usually 'local')
TEMPLATE_STORAGE=""
for s in "local" "local-lvm" "local-zfs"; do
    if pvesm status -storage "$s" 2>/dev/null | grep -q "active"; then
        if pvesm list "$s" -content vztmpl &>/dev/null; then
            TEMPLATE_STORAGE="$s"
            break
        fi
    fi
done
if [ -z "$TEMPLATE_STORAGE" ]; then
    TEMPLATE_STORAGE="local"
fi

# Set defaults
CT_ID="$NEXT_CT_ID"
HOSTNAME="athenea"
CORES="2"
RAM="2048"
SWAP="512"
DISK_SIZE="16"
STORAGE="$DEFAULT_STORAGE"
BRIDGE="vmbr0"
IP_CONFIG="dhcp"
UNPRIVILEGED="1"

# Advanced configuration prompts if selected
if [ "$SETTINGS_MODE" == "Advanced" ]; then
    CT_ID=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Container ID" --inputbox "Enter Container ID:" 10 60 "$CT_ID" 3>&1 1>&2 2>&3) || exit 0
    HOSTNAME=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Container Hostname" --inputbox "Enter Hostname:" 10 60 "$HOSTNAME" 3>&1 1>&2 2>&3) || exit 0
    
    # Available storage list
    AVAILABLE_STORAGES=$(pvesm status -content rootdir | awk 'NR>1 {print $1, $2}')
    STORAGE=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Disk Storage Pool" --inputbox "Enter Storage Pool ($AVAILABLE_STORAGES):" 10 60 "$STORAGE" 3>&1 1>&2 2>&3) || exit 0
    
    DISK_SIZE=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Disk Size" --inputbox "Enter Disk Size in GB:" 10 60 "$DISK_SIZE" 3>&1 1>&2 2>&3) || exit 0
    CORES=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "CPU Cores" --inputbox "Enter CPU Cores:" 10 60 "$CORES" 3>&1 1>&2 2>&3) || exit 0
    RAM=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "RAM (MB)" --inputbox "Enter RAM in MB:" 10 60 "$RAM" 3>&1 1>&2 2>&3) || exit 0
    SWAP=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Swap (MB)" --inputbox "Enter Swap in MB:" 10 60 "$SWAP" 3>&1 1>&2 2>&3) || exit 0
    BRIDGE=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Network Bridge" --inputbox "Enter Network Bridge:" 10 60 "$BRIDGE" 3>&1 1>&2 2>&3) || exit 0
    
    IP_METHOD=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "IP Assignment" --radiolist "Choose IP Configuration:" 10 60 2 \
        "dhcp" "Automatic via DHCP (Recommended)" ON \
        "static" "Manual Static IP (CIDR format)" OFF \
        3>&1 1>&2 2>&3) || exit 0
        
    if [ "$IP_METHOD" == "static" ]; then
        STATIC_IP=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Static IP" --inputbox "Enter Static IP with CIDR (e.g., 192.168.1.50/24):" 10 60 "192.168.1.50/24" 3>&1 1>&2 2>&3) || exit 0
        GATEWAY=$(whiptail --backtitle "Proxmox VE - Athenea Installer" --title "Gateway" --inputbox "Enter Gateway IP (e.g., 192.168.1.1):" 10 60 "192.168.1.1" 3>&1 1>&2 2>&3) || exit 0
        IP_CONFIG="${STATIC_IP},gw=${GATEWAY}"
    else
        IP_CONFIG="dhcp"
    fi
fi

# Validate CT ID does not already exist
if pct status "$CT_ID" &>/dev/null; then
    msg_err "Container ID $CT_ID already exists on this Proxmox node!"
fi

# 4. Debian 12 Template Verification / Download
msg_info "Updating Proxmox appliance template catalog..."
pveam update >/dev/null 2>&1 || true

msg_info "Locating Debian 12 Standard template..."
TEMPLATE_NAME=$(pveam available -section system | awk '{print $2}' | grep "debian-12-standard" | head -n 1)

if [ -z "$TEMPLATE_NAME" ]; then
    msg_err "Could not find Debian 12 template in Proxmox appliance index."
fi

# Check if already downloaded
CACHED_TEMPLATE=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | grep "debian-12-standard" | head -n 1 || true)
if [ -z "$CACHED_TEMPLATE" ]; then
    msg_info "Downloading template '$TEMPLATE_NAME' to storage '$TEMPLATE_STORAGE'..."
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE_NAME"
    TEMPLATE_PATH="${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE_NAME}"
else
    msg_info "Using cached template: $CACHED_TEMPLATE"
    TEMPLATE_PATH="$CACHED_TEMPLATE"
fi

# 5. Create LXC Container
msg_info "Creating LXC Container $CT_ID ($HOSTNAME)..."
pct create "$CT_ID" "$TEMPLATE_PATH" \
    --ostype debian \
    --hostname "$HOSTNAME" \
    --cores "$CORES" \
    --memory "$RAM" \
    --swap "$SWAP" \
    --rootfs "${STORAGE}:${DISK_SIZE}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=${IP_CONFIG},firewall=1" \
    --unprivileged "$UNPRIVILEGED" \
    --onboot 1 \
    --description "<div align='center'><a href='http://github.com/atheneaelib-elib/project-athenea'><img src='https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/static/img/athenea-logo.png' width='120'/></a><h2>Athenea eBook Library</h2><p>High-Performance Digital Media Server</p></div>"

# Configure Container Features (nesting & keyctl required for modern venv/docker/systemd in unprivileged CT)
msg_info "Setting container features (nesting=1, keyctl=1)..."
pct set "$CT_ID" -features nesting=1,keyctl=1

# 6. Start Container & Wait for IP
msg_info "Starting LXC container $CT_ID..."
pct start "$CT_ID"

msg_info "Waiting for container to initialize network..."
CT_IP=""
for i in {1..30}; do
    CT_IP=$(pct exec "$CT_ID" -- ip -4 addr show eth0 2>/dev/null | awk '/inet / {print $2}' | cut -d/ -f1 || true)
    if [ -n "$CT_IP" ]; then
        break
    fi
    sleep 1
done

if [ -z "$CT_IP" ]; then
    msg_warn "Could not automatically resolve IPv4 address via eth0. Falling back to container localhost check."
    CT_IP="<CONTAINER_IP>"
else
    msg_ok "Container acquired IP address: $CT_IP"
fi

# Check for GitHub token (for private repositories)
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

# 7. Provision Athenea inside Container
msg_info "Executing Athenea provisioning inside container $CT_ID..."
INSTALL_SCRIPT=""
if [ -n "$GITHUB_TOKEN" ]; then
    msg_info "Using GitHub Token for authenticated private repository access..."
    INSTALL_SCRIPT=$(curl -fsSL -H "Authorization: token $GITHUB_TOKEN" -H "Accept: application/vnd.github.v3.raw" https://api.github.com/repos/atheneaelib-elib/project-athenea/contents/install/athenea-install.sh || true)
else
    INSTALL_SCRIPT=$(wget -qLO - https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/install/athenea-install.sh 2>/dev/null || curl -fsSL https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/install/athenea-install.sh 2>/dev/null || true)
fi

# If public download returned 404 or empty, prompt interactively for GitHub token
if [ -z "$INSTALL_SCRIPT" ] || echo "$INSTALL_SCRIPT" | grep -q "404"; then
    msg_warn "Public download returned 404 (private repository detected)."
    GITHUB_TOKEN=$(whiptail --backtitle "Proxmox VE - Athenea Installer" \
        --title "GitHub Private Repository Token" \
        --passwordbox "The repository appears to be Private.\n\nPlease enter your GitHub Personal Access Token (ghp_...):" 11 68 3>&1 1>&2 2>&3 || true)
    
    if [ -n "$GITHUB_TOKEN" ]; then
        msg_info "Retrying provisioning download with provided token..."
        INSTALL_SCRIPT=$(curl -fsSL -H "Authorization: token $GITHUB_TOKEN" -H "Accept: application/vnd.github.v3.raw" https://api.github.com/repos/atheneaelib-elib/project-athenea/contents/install/athenea-install.sh || true)
    fi
fi

if [ -z "$INSTALL_SCRIPT" ] || echo "$INSTALL_SCRIPT" | grep -q "404"; then
    msg_err "Failed to download provisioning script from GitHub.\nIf the repository is private, supply a valid token with 'repo' scope, or make the repository Public."
fi

echo "$INSTALL_SCRIPT" | pct exec "$CT_ID" -- env GITHUB_TOKEN="$GITHUB_TOKEN" bash

# Verify service is running on port 8000 inside the container
msg_info "Verifying Athenea service on port 8000..."
sleep 3
if ! pct exec "$CT_ID" -- ss -tulpn | grep -q ":8000"; then
    msg_warn "Athenea service is still starting up or encountered an issue. Check logs with: pct exec $CT_ID -- journalctl -u athenea -n 50"
fi

# 8. Success Output
msg_ok "Athenea has been successfully installed in Container $CT_ID!"

whiptail --backtitle "Proxmox VE - Athenea Installer" \
    --title "Installation Complete!" \
    --msgbox "🎉 Athenea has been successfully installed and started!\n\nContainer ID: $CT_ID\nContainer IP: $CT_IP\n\nAccess the Setup Wizard in your web browser:\n👉 http://${CT_IP}:8000/setup\n\nBook storage inside container is located at:\n📂 /data/books" 14 70

echo -e "\n${GREEN}${BOLD}==============================================================================${NC}"
echo -e "${GREEN}${BOLD}   🎉 Athenea LXC Container $CT_ID Successfully Installed! 🎉${NC}"
echo -e "${GREEN}${BOLD}==============================================================================${NC}\n"
echo -e "Web Setup Wizard:  ${CYAN}${BOLD}http://${CT_IP}:8000/setup${NC}"
echo -e "Container ID:      ${CYAN}$CT_ID${NC}"
echo -e "Hostname:          ${CYAN}$HOSTNAME${NC}"
echo -e "IPv4 Address:      ${CYAN}$CT_IP${NC}"
echo -e "Books Directory:   ${CYAN}/data/books${NC} (inside CT $CT_ID)"
echo -e "\nTo access the container shell:"
echo -e "👉 ${BOLD}pct enter $CT_ID${NC}\n"
