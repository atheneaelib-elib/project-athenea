<div align="center">

<img src="static/brand/banner.jpg" alt="Athenea Banner" width="100%" style="border-radius: 12px; max-width: 900px; box-shadow: 0 10px 30px rgba(0,0,0,0.3);" />

<br/><br/>

# 🏛️ Athenea
### *The Next-Generation Self-Hosted Digital Library & Neural Audiobook Platform*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

<p align="center">
  <strong>Engineered for Massive Scale (500,000+ Books) &bull; In-Browser Reading &bull; Neural TTS Narration &bull; OPDS 1.2 Feed &bull; Send to Kindle</strong>
</p>

[Proxmox VE LXC](#-proxmox-ve-community-installer) •
[Linux One-Liner](#-universal-linux-automated-install) •
[Docker Compose](#-quick-start-with-docker-compose) •
[Features](#-features) •
[Setup Wizard](#-first-run-setup-wizard) •
[OPDS Support](#-opds-catalog-support) •
[Manual Setup](#-manual-native-installation)

</div>

---

## 🌟 Highlights

**Athenea** is a state-of-the-art, private digital library system built to organize, search, read, and listen to massive eBook collections. Named after Athena, the classical goddess of wisdom, literature, and learning, Athenea is custom-architected with PostgreSQL trigram indexing (`pg_trgm`) and GIN indices to deliver **instant sub-millisecond full-text fuzzy search across collections of 500,000+ books**.

---

## ⚡ Features

### 📚 Massive Scale & Lightning Fast Catalog
- **Engineered for 500k+ Books**: Instant response times even with gigabyte-scale relational databases.
- **PostgreSQL Trigram Search**: Fast typo-tolerant fuzzy matching across titles, authors, series, and tags.
- **Universal Format Support**: ePub, PDF, MOBI, AZW3, FB2, CBZ, CBR, DJVU, and TXT.
- **High-Performance Cover Generation**: Embedded cover extraction and caching via Pillow.

### 📖 Immersive In-Browser Reader
- **Zero Install Needed**: Clean, responsive, distraction-free reading experience directly in your browser.
- **Customizable Typography**: Custom fonts, font sizes, line spacing, margins, and themes (Dark, Sepia, AMOLED Black, Paper White).
- **Progress Tracking & Bookmarks**: Reading position automatically syncs across all your devices.

### 🎙️ In-Browser Neural Audiobook Narration (TTS)
- **High-Fidelity Neural Voices**: Listen to any eBook with crystal-clear neural voices powered by Edge TTS and Kokoro/Piper ONNX models.
- **Live Text Highlighting**: Follow along with auto-scrolling synchronized narration.
- **Zero Client Overhead**: Deterministic audio generation and playback.

### 📱 OPDS 1.2 Catalog Integration
- **Direct e-Reader Sync**: Full compatibility with **KOReader**, **Moon+ Reader**, **FBReader**, and other popular mobile/e-ink reader apps.
- **Faceted Feeds**: Browse by Recent, Authors, Series, Tags, or search directly from your reading hardware over LAN/WAN.
- **Basic Auth Protected**: Safe multi-user access for hardware readers.

### 📨 1-Click Send to Kindle
- **Amazon Delivery Integration**: Send books directly to your `@kindle.com` email address with automatic format validation.

### 📊 Reading Activity & Streaks
- **Personal Insights**: Comprehensive reading statistics, daily streaks, hours listened, books finished, and monthly reading goals.

### 🚀 First-Run Visual Setup Wizard
- **Web-Based Initializer**: Intuitive first-run wizard at `/setup` to test your database connection, initialize the schema, configure storage paths, and provision the primary superadmin account.
- **Automatic Lockdown**: Once an administrator exists, `/setup` is permanently guarded and returns `403 Forbidden`.

---

## 🚀 Easy Installation Options

Choose the installation method that fits your environment:

### 📦 Proxmox VE Community Installer (LXC)
Run this command in the **Proxmox VE Node Shell** to automatically provision a dedicated Debian 12 LXC container:
```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/ct/athenea.sh)"
```
*Alternatively, you can also use `https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/install/proxmox.sh`.*

- ⚡ **Interactive Menu**: Choose Default (2 Cores, 2GB RAM, 16GB Disk) or Advanced settings.
- 🔒 **Secure**: Sets up an unprivileged container with nesting and keyctl enabled.
- 📦 **Automated**: Automatically provisions PostgreSQL, Python 3 virtual environment, storage directories (`/data/books`), and systemd daemon.

---

### 🐧 Universal Linux Automated Install
Run this one-liner on any fresh **Debian 11/12**, **Ubuntu 20.04/22.04/24.04**, or compatible Linux host:
```bash
curl -fsSL https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/install.sh | sudo bash
```
What this script does:
1. Installs PostgreSQL, Python 3, build tools, and media processing libraries.
2. Creates system user `athenea` and sets up `/data/books` library storage.
3. Generates secure credentials and initializes the database.
4. Clones the repository to `/opt/athenea` and installs Python dependencies.
5. Deploys and starts the `athenea.service` systemd unit.
6. Opens port `8000` with the First-Run Setup Wizard ready at `http://<IP>:8000/setup`.

---

## 🐳 Quick Start with Docker Compose

The easiest and recommended way to run Athenea is using Docker Compose:

### 1. Clone the repository
```bash
git clone https://github.com/atheneaelib-elib/project-athenea.git
cd project-athenea
```

### 2. Configure environment
Copy the sample environment file and customize your storage paths:
```bash
cp .env.example .env
```

Edit `.env` to point `HOST_BOOKS_PATH` to your book folder:
```ini
HOST_BOOKS_PATH=/path/to/your/ebooks
POSTGRES_PASSWORD=choose_a_strong_database_password
SESSION_SECRET=create_a_random_session_secret_key
```

### 3. Launch Athenea
```bash
docker compose up -d
```

### 4. Complete Setup Wizard
Open your browser and navigate to:
```
http://localhost:8000
```
Follow the interactive **First-Run Setup Wizard** to configure your database and create your administrator account!

---

## 🛠️ First-Run Setup Wizard

On fresh installations, Athenea automatically detects that no admin user exists and directs all incoming requests to the **Setup Wizard** (`/setup`):

<div align="center">
  <img src="static/brand/icon.jpg" width="96" alt="Athenea Owl of Wisdom" />
</div>

1. **Database Verification**: Test connectivity to your PostgreSQL instance with 1-click.
2. **Schema Deployment**: Automatically deploys tables, foreign keys, `pg_trgm` extensions, and GIN indices.
3. **Admin Provisioning**: Create your primary administrator credentials.
4. **Registration Security**: Choose whether public user registration is open, requires admin approval, or is invite-only.

---

## 📱 OPDS Catalog Support

Connect your favorite e-reader app to Athenea:

- **OPDS Feed URL**: `http://<your-server-ip>:8000/opds`
- **Authentication**: HTTP Basic Auth (Use your Athenea username & password)

Compatible with:
- **KOReader** (Kindle, Kobo, Android)
- **Moon+ Reader** (Android)
- **FBReader** (Android, iOS)
- **Chunky Reader** (iPad)

---

## ⚙️ Configuration & Environment Variables

All settings can be configured via environment variables or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `localhost` | PostgreSQL database host |
| `DB_PORT` | `5432` | PostgreSQL database port |
| `DB_NAME` | `athenea` | PostgreSQL database name |
| `DB_USER` | `athenea` | PostgreSQL database user |
| `DB_PASSWORD` | *(required)* | PostgreSQL database password |
| `LIBRARY_ROOT` | `/data/books` | Absolute path to library root directory |
| `THUMBNAIL_DIR` | `/data/thumbnails` | Directory for cached cover thumbnails |
| `SESSION_SECRET` | *(required)* | Secret key for signed session cookies |
| `SESSION_COOKIE_NAME` | `book_session` | Name of the authentication cookie |
| `DEFAULT_PAGE_SIZE` | `48` | Books rendered per catalog page |
| `ENABLE_DOCS` | `false` | Enable Swagger `/docs` and `/openapi.json` |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SMTP_HOST` | `""` | SMTP server for Send-to-Kindle |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_USER` | `""` | SMTP username |
| `SMTP_PASSWORD` | `""` | SMTP password |

---

## 🐧 Manual Native Installation

For bare-metal Linux servers (Ubuntu/Debian):

### 1. Install System Dependencies
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv postgresql postgresql-contrib libpq-dev
```

### 2. Configure PostgreSQL
```bash
sudo -u postgres psql
```
```sql
CREATE DATABASE athenea;
CREATE USER athenea WITH ENCRYPTED PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE athenea TO athenea;
\c athenea
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
\q
```

### 3. Initialize Python Virtualenv
```bash
git clone https://github.com/atheneaelib-elib/project-athenea.git /opt/athenea
cd /opt/athenea
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Create Systemd Service
Create `/etc/systemd/system/athenea.service`:
```ini
[Unit]
Description=Athenea eBook & Audiobook Server
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=athenea
Group=athenea
WorkingDirectory=/opt/athenea
ExecStart=/opt/athenea/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips='*'
Restart=always
RestartSec=3
EnvironmentFile=/etc/athenea.env

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now athenea
```

---

## 🔒 Security

- **Safe Path Traversal Guards**: Every file access is strictly validated against `assert_safe_path` to prevent path traversal exploits (`../../`).
- **Brute Force Protection**: Rate-limited authentication endpoints.
- **Fail2ban Filter & Jail**: Preconfigured fail2ban rules included in `deploy/`.
- **Admin Lockdown**: Critical settings and `/setup` endpoints are completely blocked from unauthorized users.

---

## 📄 License

Athenea is open-source software licensed under the **[MIT License](LICENSE)**.
