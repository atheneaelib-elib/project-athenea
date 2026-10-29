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
  <strong>Engineered for Massive Scale (500,000+ Books) &bull; In-Browser Reading &bull; Neural TTS Narration &bull; Granular RBAC Permissions &bull; OPDS 1.2 Feed &bull; Send to Kindle</strong>
</p>

[Docker Compose](#-option-1-quick-start-with-docker-compose) •
[Proxmox VE LXC](#-option-2-proxmox-ve-community-installer-lxc) •
[Debian/Ubuntu Script](#-option-3-universal-debianubuntu-automated-script) •
[Manual Setup](#-option-4-manual-native-installation) •
[Features](#-features) •
[Setup Wizard](#-first-run-setup-wizard) •
[Admin & Permissions](#-granular-permissions--admin-settings) •
[OPDS Feeds](#-opds-catalog-support)

</div>

---

## 🌟 Highlights

**Athenea** is a state-of-the-art, private digital library system built to organize, search, read, and listen to massive eBook collections. Named after Athena, the classical goddess of wisdom, literature, and learning, Athenea is custom-architected with PostgreSQL trigram indexing (`pg_trgm`) and GIN indices to deliver **instant sub-millisecond full-text fuzzy search across collections of 500,000+ books**.

---

## ⚡ Features

### 📚 Massive Scale & Lightning Fast Catalog
- **Engineered for 500,000+ Books**: Sub-millisecond response times even with gigabyte-scale relational databases.
- **PostgreSQL Trigram Search**: Fast typo-tolerant fuzzy matching across titles, authors, series, and tags.
- **Universal Format Support**: ePub, PDF, MOBI, AZW3, FB2, CBZ, CBR, DJVU, and TXT.
- **High-Performance Cover Generation**: Embedded cover extraction and caching with instant WebP thumbnail generation.
- **Robust CLI Tooling**: Background bulk import, library synchronization, deduplication engine, and interactive safe book deletion.

### 📖 Immersive In-Browser Reader
- **Zero Client Install**: Clean, responsive, distraction-free reading experience directly in your browser.
- **7 Curated Visual Themes**: Slate (Default Dark), Light, Sepia, Nord, Forest, Dracula, and OLED Black with instant live switching.
- **Customizable Typography**: Custom fonts, font sizes, line height, margins, and reading flow.
- **Cross-Device Progress Tracking**: Reading position, bookmarks, notes, and highlights automatically sync across devices.

### 🎙️ In-Browser Neural Audiobook Narration (TTS)
- **High-Fidelity Neural Voices**: Listen to any eBook with crystal-clear neural voices powered by Edge TTS and Kokoro/Piper ONNX models.
- **Live Text Highlighting**: Follow along with auto-scrolling synchronized narration.
- **Zero Client Overhead**: Deterministic audio streaming and playback directly in the browser.

### 🛡️ Granular Role-Based Access Control (RBAC)
- **Superadmin, Admin, and User Roles**: Granular permission matrix controlling uploads, downloads, Kindle delivery, TTS narration, metadata editing, and book deletion.
- **Per-User Overrides**: Customize individual user privileges directly from the Admin Settings dashboard.
- **User Approval Flow**: Moderate public registration with optional mandatory administrator approval.

### 📱 OPDS 1.2 Catalog Integration
- **Direct Hardware & App Sync**: Full compatibility with **KOReader**, **Moon+ Reader**, **FBReader**, and other popular mobile/e-ink reader apps.
- **Faceted Feeds**: Browse by Recent, Authors, Series, Tags, or search directly from your reading hardware over LAN or WAN.
- **HTTP Basic Auth**: Safe multi-user access for hardware readers and mobile apps.

### 📨 1-Click Send to Kindle
- **Amazon Delivery Integration**: Send books directly to your `@kindle.com` email address with automatic format validation.
- **Intelligent Encryption Support**: Auto-detects STARTTLS (Port 587) and Direct SSL (Port 465) with Google App Password support and live diagnostic connection testing.

### 📊 Reading Activity & Streaks
- **Personal Insights**: Comprehensive reading statistics, daily streaks, hours listened, books finished, and monthly reading goals.

### 🚀 First-Run Visual Setup Wizard
- **Universal Web Initializer**: Intuitive first-run wizard at `/setup` across Docker, Proxmox LXC, Linux scripts, and bare-metal installs.
- **Automated Lockdown**: Once the primary superadmin is created, `/setup` is permanently sealed and guarded.

---

## 🚀 Installation Options

Choose the installation method that fits your environment:

---

### 🐳 Option 1: Quick Start with Docker Compose

The easiest and recommended way to run Athenea in containers:

#### 1. Clone the repository
```bash
git clone https://github.com/atheneaelib-elib/project-athenea.git
cd project-athenea
```

#### 2. Configure environment
Copy the sample environment template:
```bash
cp .env.example .env
```

Edit `.env` to point `LIBRARY_ROOT` to your local book directory and set your passwords:
```ini
LIBRARY_ROOT=/path/to/your/ebooks
DB_PASSWORD=choose_a_strong_database_password
SESSION_SECRET=create_a_random_session_secret_key
```

#### 3. Start the containers
```bash
docker compose up -d --build
```

#### 4. Complete Setup Wizard
Open your browser and navigate to:
```
http://localhost:8000
```
Follow the interactive **First-Run Setup Wizard** to initialize the database and create your superadmin account!

---

### 📦 Option 2: Proxmox VE Community Installer (LXC)

Run this single command in your **Proxmox VE Node Shell** to automatically provision a dedicated Debian 12 LXC container:

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/ct/athenea.sh)"
```

* ⚡ **Interactive Menu**: Choose Default (2 Cores, 2GB RAM, 16GB Disk) or Advanced custom settings.
* 🔒 **Secure**: Sets up an unprivileged container with nesting and keyctl enabled.
* 📦 **Fully Automated**: Automatically provisions PostgreSQL 16, Python 3 virtual environment, storage directories (`/data/books`), and the `athenea.service` systemd unit.
* 🌐 **Ready Immediately**: Navigate to `http://<CONTAINER-IP>:8000` to complete the Setup Wizard.

---

### 🐧 Option 3: Universal Debian/Ubuntu Automated Script

Run this command on any fresh **Debian 11/12**, **Ubuntu 20.04/22.04/24.04**, or compatible Linux host:

```bash
curl -fsSL https://raw.githubusercontent.com/atheneaelib-elib/project-athenea/main/install.sh | sudo bash
```

**What this script handles automatically:**
1. Installs PostgreSQL 16, Python 3.12+, build essentials, and media processing libraries.
2. Creates the system user and group `athenea`.
3. Creates `/data/books` library root and `/data/backups` directories.
4. Generates secure database credentials and deploys the schema.
5. Clones Athenea to `/opt/athenea` and provisions the Python virtual environment.
6. Deploys, enables, and starts the `athenea.service` systemd daemon.
7. Opens port `8000` ready for the First-Run Setup Wizard.

---

### 🛠️ Option 4: Manual Native Installation

For bare-metal Linux servers and advanced system administrators:

#### 1. Install System Dependencies
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv postgresql postgresql-contrib libpq-dev git curl
```

#### 2. Configure PostgreSQL
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

#### 3. Clone Repository and Set Up Virtual Environment
```bash
git clone https://github.com/atheneaelib-elib/project-athenea.git /opt/athenea
cd /opt/athenea
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 4. Deploy Systemd Unit
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

Enable and start Athenea:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now athenea
```

---

## 🛠️ First-Run Setup Wizard

On fresh installations, Athenea automatically detects that no admin user exists and directs all incoming requests to the **Setup Wizard** (`/setup`):

<div align="center">
  <img src="static/brand/icon.jpg" width="96" alt="Athenea Owl of Wisdom" style="border-radius: 50%;" />
</div>

1. **Database Verification**: Test connectivity to your PostgreSQL instance with 1-click.
2. **Schema Deployment**: Automatically deploys tables, foreign keys, `pg_trgm` extensions, and GIN indices.
3. **Superadmin Provisioning**: Create your primary administrator username and password.
4. **Registration Policy**: Choose whether public user registration is open, requires admin approval, or is invite-only.

---

## 🛡️ Granular Permissions & Admin Settings

Athenea includes an administrative suite accessible at `/admin/settings` (and `/admin/users`):

* **Role-Based Permissions**:
  * `can_upload`: Upload books via web interface or API.
  * `can_download`: Download eBook files.
  * `can_email_kindle`: Send books to Amazon Kindle or personal email.
  * `can_stream_tts`: In-browser neural speech narration.
  * `can_edit_metadata`: Edit book titles, authors, series, and tags.
  * `can_delete_books`: Delete books from the catalog with optional physical disk file removal.
* **Database & Pool Reconfiguration**: Update PostgreSQL connection parameters with live hot-reloading.
* **SMTP Diagnostics**: Test email delivery with live diagnostic ping and auto-detection of STARTTLS vs SSL.
* **Fail2ban Integration**: View active failed logins, banned IPs, and unban addresses with 1-click.

---

## 📱 OPDS Catalog Support

Connect your favorite e-reader app to Athenea:

* **OPDS Feed URL**: `http://<your-server-ip>:8000/opds`
* **Authentication**: HTTP Basic Auth (Use your Athenea username & password)

**Tested Hardware & Apps:**
* **KOReader** (Kindle, Kobo, Android, Linux)
* **Moon+ Reader** (Android)
* **FBReader** (Android, iOS)
* **Chunky Reader** (iPad)

---

## ⚙️ Configuration & Environment Variables

All settings can be configured via environment variables, `.env`, or `/etc/athenea.env`:

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `localhost` | PostgreSQL database host |
| `DB_PORT` | `5432` | PostgreSQL database port |
| `DB_NAME` | `athenea` | PostgreSQL database name |
| `DB_USER` | `athenea` | PostgreSQL database user |
| `DB_PASSWORD` | *(required)* | PostgreSQL database password |
| `LIBRARY_ROOT` | `/data/books` | Absolute path to library root directory |
| `BACKUP_DIR` | `/data/backups` | Target directory for database backups |
| `SESSION_SECRET` | *(required)* | Secret key for signed session cookies |
| `SESSION_COOKIE_NAME` | `book_session` | Name of the authentication cookie |
| `COOKIE_SECURE` | `false` | Set to `true` when running behind HTTPS proxy |
| `ALLOW_REGISTRATION` | `false` | Allow public visitors to register on `/register` |
| `REQUIRE_ADMIN_APPROVAL` | `true` | Require admin approval before new users can log in |
| `DEFAULT_PAGE_SIZE` | `48` | Books rendered per catalog page |
| `ENABLE_DOCS` | `false` | Enable Swagger `/docs` and `/openapi.json` |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SMTP_HOST` | `""` | SMTP server for Send-to-Kindle (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | `587` | SMTP server port (`587` for STARTTLS, `465` for SSL) |
| `SMTP_USER` | `""` | SMTP authentication username |
| `SMTP_PASSWORD` | `""` | SMTP password or Google App Password |
| `SMTP_FROM` | `books@yourdomain.com` | From address for outgoing book emails |

---

## 🔒 Security

* **Path Traversal Guards**: Every file access is strictly validated against `assert_safe_path` to prevent directory traversal exploits (`../../`).
* **Session Cookie Hardening**: HTTP-only, SameSite=Lax signed session cookies with optional secure flag.
* **Brute-Force Protection**: IP rate-limiting on authentication endpoints and built-in fail2ban jail integration.
* **Admin Lockdown**: All `/admin/*` and `/setup` endpoints require authenticated administrator session tokens.
* **Responsible Disclosure**: Review [SECURITY.md](SECURITY.md) for vulnerability reporting guidelines.

---

## 📄 License

Athenea is open-source software licensed under the **[MIT License](LICENSE)**.
