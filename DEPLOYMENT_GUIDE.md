# Production Deployment & Hosting Guide — NEXUS KEYS

This guide provides complete, production-grade instructions for taking the **NEXUS KEYS** digital products and gaming services store live onto the public internet.

---

## 1. Quick Start: Deploy Anywhere with Docker (Recommended)

The project includes an enterprise-grade `Dockerfile` and `docker-compose.yml`.

### On Any VPS (Ubuntu, Debian, AlmaLinux, DigitalOcean Droplet, AWS EC2):

1. **Install Docker & Docker Compose**:
   ```bash
   curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh
   ```

2. **Upload & Extract Project Files**:
   ```bash
   mkdir -p /opt/nexus-keys && cd /opt/nexus-keys
   # Copy or upload the project archive here
   unzip nexus-keys-production-release.zip
   ```

3. **Configure Environment Variables**:
   ```bash
   cp .env.example .env
   nano .env
   ```
   *Fill in your `SECRET_KEY`, `ADMIN_SECRET_URL`, and SMTP credentials.*

4. **Launch with 1 Command**:
   ```bash
   docker compose up -d --build
   ```

5. **Verify Running Container**:
   ```bash
   docker compose ps
   docker compose logs -f
   ```

Your platform is now live on port `8000` with automated health checks, automatic restarts (`restart: unless-stopped`), and persistent database storage mounted at `./data`.

---

## 2. Cloud Platform Deployment (Render, Railway, Fly.io)

### Deploying to Render (render.com):
1. Push your code to a private GitHub/GitLab repository.
2. In Render, select **"New Web Service"** and connect your repository.
3. Choose **Docker** as the environment (Render detects `Dockerfile` automatically).
4. Add Environment Variables in the Render dashboard:
   - `SECRET_KEY`: Set a long random string
   - `ADMIN_SECRET_URL`: `/portal-vault-admin` (or your custom admin path)
   - `SMTP_HOST`: `smtp.gmail.com`
   - `SMTP_USER`: `your_email@gmail.com`
   - `SMTP_PASSWORD`: `your_16_char_google_app_password`
   - `SMTP_FROM_EMAIL`: `your_email@gmail.com`
5. Click **Deploy**. Render provisions SSL, automated deployment, and gives you a free HTTPS subdomain (e.g., `https://nexus-keys.onrender.com`).
6. *(Optional)* Add a Persistent Disk on Render mounted at `/app/data` to persist data across zero-downtime rebuilds.

### Deploying to Railway (railway.app):
1. Connect your GitHub repository.
2. Railway detects the `Dockerfile` automatically.
3. Attach a Persistent Volume to `/app/data`.
4. Add your `.env` variables under the **Variables** tab.
5. Generate a Public Domain or connect your custom domain with instant SSL.

---

## 3. Traditional VPS Deployment (Systemd + Nginx + Let's Encrypt SSL)

For maximum performance on a dedicated Ubuntu 22.04 / 24.04 LTS server:

### Step 1: Install System Packages
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv nginx certbot python3-certbot-nginx libjpeg-dev zlib1g-dev
```

### Step 2: Clone or Extract to `/var/www/nexuskeys`
```bash
sudo mkdir -p /var/www/nexuskeys
sudo chown -R $USER:$USER /var/www/nexuskeys
cd /var/www/nexuskeys
# Extract files here
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Step 3: Configure Systemd Service
Create `/etc/systemd/system/nexuskeys.service`:
```ini
[Unit]
Description=NEXUS KEYS Digital Commerce Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/nexuskeys
Environment="PATH=/var/www/nexuskeys/venv/bin"
EnvironmentFile=/var/www/nexuskeys/.env
ExecStart=/var/www/nexuskeys/venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Set permissions, enable and start:
```bash
sudo chown -R www-data:www-data /var/www/nexuskeys
sudo systemctl daemon-reload
sudo systemctl enable nexuskeys
sudo systemctl start nexuskeys
sudo systemctl status nexuskeys
```

### Step 4: Configure Nginx Reverse Proxy with SSL
Copy [`nginx.conf.example`](./nginx.conf.example) to `/etc/nginx/sites-available/nexuskeys`:
```bash
sudo cp nginx.conf.example /etc/nginx/sites-available/nexuskeys
sudo ln -s /etc/nginx/sites-available/nexuskeys /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Obtain a free SSL Certificate with automatic renewals:
```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

---

## 4. Production Security & Launch Checklist

- [x] **Change Default Passwords**:
  - Super Admin: `admin@vault.internal` &rarr; update password in Admin Center or database.
  - Customer Demo: `client@store.com` &rarr; update or disable.
- [x] **Configure Production Secret Key**: Set a unique 64-character `SECRET_KEY` in `.env`.
- [x] **Configure Secret Admin Path**: Set `ADMIN_SECRET_URL` (e.g. `/vault-secure-gate-2026`).
- [x] **Configure Outgoing SMTP**: Connect your Gmail App Password or SendGrid API key so password resets, PDF receipts, and restock alerts deliver live.
- [x] **Automated Nightly Backups**:
  Add a cron job to create daily database snapshots:
  ```bash
  crontab -e
  # Add:
  0 3 * * * cd /var/www/nexuskeys && /var/www/nexuskeys/venv/bin/python backup.py create >> /var/log/nexus_backup.log 2>&1
  ```
