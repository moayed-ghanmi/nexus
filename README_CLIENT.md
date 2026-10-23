# NEXUS KEYS — Digital Products & Gaming Services Platform
## Official Client Delivery Package (v1.0.0 Commercial Release)

Welcome to your new **NEXUS KEYS** e-commerce platform! This package contains the complete, production-ready codebase, pre-seeded database, user interface assets, and deployment configurations.

---

## 🔑 1. Default Access Credentials

| Portal | Access URL | Email | Password | Role / Details |
| :--- | :--- | :--- | :--- | :--- |
| **Super Admin** | `/portal-vault-admin` | `admin@vault.internal` | `admin_vault_2026!` | Full Master Admin Access |
| **Demo Customer** | `/auth/login` | `client@store.com` | `client123` | Pre-funded ($68.33 Wallet Balance) |
| **Test Customer** | `/auth/login` | `psmoayed22@gmail.com` | *(configured)* | Pre-funded ($100.00 Wallet Balance) |

> ⚠️ **Important First Step**: After logging into the Admin Portal, go to **Admin Center > Admins** and update the default password for `admin@vault.internal`.

---

## 💳 2. Configured Payment Methods

The platform is exclusively set up with the following payment channels:

1. **Nexus Wallet / Store Credit**:
   - 1-Click Instant Payment with zero processor fees.
   - Automatically debits customer store balance and delivers digital keys in real time.
2. **Paiement Mobile D17 / Flouci**:
   - Number: `+216 98 765 432` *(Titulaire: NEXUS KEYS)*.
   - Customer can attach proof-of-payment receipts (JPG, PNG, PDF) and enter transaction notes.
   - Creates orders in `PENDING` status for admin review.
3. **Virement Bancaire Direct (R.I.B)**:
   - Official R.I.B: `08 015 00011122233344 55` *(BIAT / Attijari Bank)*.
   - Customer attaches bank transfer proof. Orders are verified and keys released by admin in 1 click.

---

## 🚀 3. Quick Start Guide

### Option A: Running Locally (Windows, Mac, Linux)
1. Ensure Python 3.10+ is installed.
2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the application:
   ```bash
   python run.py
   ```
4. Access the store:
   - **Storefront**: http://127.0.0.1:8000
   - **Admin Gate**: http://127.0.0.1:8000/portal-vault-admin

---

### Option B: Running with Docker (Recommended for Servers)
1. Install Docker & Docker Compose on your server.
2. Run:
   ```bash
   docker compose up -d --build
   ```
3. The platform starts on port `8000` with automatic restarts and persistent storage in `./data`.

---

### Option C: Free 24/7 Cloud Hosting on Render.com
1. Create a repository on [GitHub](https://github.com) and upload these files.
2. In [Render.com](https://render.com), create a **New Web Service** and connect your GitHub repo.
3. Select **Docker** environment and **Free** tier.
4. Render builds and launches your website with a free HTTPS domain in ~2 minutes!
5. Add your custom domain under **Settings > Custom Domains**.

---

### Option D: Serverless on Vercel
- This project includes `vercel.json` and `api/index.py` configured for Vercel deployment.
- Simply connect your repository to [Vercel.com](https://vercel.com) and deploy.

---

## ⚙️ 4. Outgoing SMTP Email Setup (Password Resets & PDF Receipts)

To send real emails to your customers:
1. Log into the Admin Panel at `/portal-vault-admin/login`.
2. Go to **Settings**.
3. Scroll to **Automated Customer Email Receipts & SMTP Engine**.
4. Enter your SMTP details (e.g., Gmail SMTP with a 16-character Google App Password, SendGrid, or Mailgun).
5. Click **"Send Test"** to verify, then click **"Save All Settings"**.

---

## 📁 5. Directory Structure Overview

- `app/`: FastAPI core application, controllers, security, and Cyberpunk UI templates.
- `data/`: SQLite database (`store.db`), generated PDF invoices, and email logs.
- `static/`: Frontend CSS, Lucide icons, product images, and customer uploaded receipts.
- `api/`: Vercel serverless function entrypoint.
- `Dockerfile` & `docker-compose.yml`: Production containerization files.
- `requirements.txt`: Python package dependencies.
- `run.py`: Production application runner.
- `backup.py`: Automated SQLite database backup utility.
- `DEPLOYMENT_GUIDE.md`: Comprehensive server deployment manual.

---

*Delivered by NEXUS KEYS Engineering Team — Commercial License v1.0.0*
