# WELL Launcher

WELL Launcher is a local dashboard for starting, stopping, and monitoring multiple projects through one web interface. It provides process status, real-time terminal logs, split logs, profiles, port scanning, and user roles.

This repository includes a **safe demo configuration** so anyone can clone it and try the dashboard without needing your private projects or machine paths.

## Why WELL Launcher exists

WELL Launcher was created to solve a practical daily problem: repeatedly opening terminals, remembering commands, changing directories, and starting every project one by one is slow and easy to get wrong. Instead of treating each project as a separate manual task, the launcher turns the local machine into a small, flexible command center.

From one dashboard, the user can start or stop individual projects, start a whole group with **Start All**, monitor their live terminal output, and switch between profiles depending on the task. Profiles make it possible to keep different working modes—such as everyday services, testing, maintenance, or a custom project combination—without rewriting commands every time.

The launcher is intentionally **local-first**. It is useful for daily development and self-hosted environments where supporting services may need to run together: Cloudflare tunnels, firewall-related services, local APIs, databases, file browsers, monitoring tools, and other background processes. Background services can be marked separately so commands such as **Stop Except Background** do not interrupt infrastructure that should remain online.

The goal is simple: define the project commands once, organize them into profiles, and manage the whole local environment without having to remember or manually launch every command again.

## Demo login

On a fresh clone, `users.json` does not exist yet. `server.py` automatically creates it with this default account:

| Username | Password | Role |
|---|---|---|
| `admin` | `admin` | Admin |

Change the password immediately from **Change Password** before using the launcher with real projects. `users.json` is intentionally listed in `.gitignore` because it contains password hashes and login metadata.

## Requirements

- Windows 10/11 is the primary tested environment for the local launcher, especially for the `.bat` files and Windows-based project processes.
- Python 3.10 or newer.
- Python packages: `Flask` and `requests`.
- The root `requirements.txt` contains the local launcher dependencies.

## Quick start on Windows

1. Clone the repository:

   ```bat
   git clone https://github.com/USERNAME/REPOSITORY.git
   cd REPOSITORY
   ```

2. Run the installer:

   ```bat
   -- INSTALL --.bat
   ```

3. Start the launcher:

   ```bat
   -- START_WELL_LAUNCHER --.bat
   ```

4. Open [http://localhost:9000](http://localhost:9000) if the browser does not open automatically.

5. Sign in with `admin` / `admin`, then change the password.

The installer uses the root `requirements.txt` file. To install the dependencies manually:

```bat
python -m pip install -r requirements.txt
python server.py
```

## Linux compatibility

The local launcher was developed and tested by the author on Windows. The core Python web application may work on Linux, but Linux support is currently **unverified** and is not promised yet. The Windows `.bat` files will not run natively on Linux, and any project commands, working directories, executable names, Cloudflare commands, firewall commands, or service management steps may need Linux-specific replacements.

Linux users can try the Python server manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

Before starting it, update `launcher_config.json` so each project's `cwd` and `cmd` use Linux paths and commands. This repository should therefore be considered **Windows-tested, Linux-unverified** for the local launcher. The separate `vercel-demo/` deployment is a Python serverless demo and is not affected by the local Windows `.bat` requirement.

## Demo projects

`launcher_config.json` contains projects that do not depend on private folders:

- **Demo HTTP Server** — runs `python -m http.server 8765`.
- **Demo Log Stream** — produces periodic terminal output for testing auto-scroll and split logs.
- **Demo Background Service** — runs a second HTTP server on port 8766 as an example background service.

This demo configuration is intentionally committed so a fresh clone can be used immediately for live testing. To use your own projects, edit `launcher_config.json` after cloning. Do not commit private paths, internal domains, API keys, tokens, or credentials to a public repository.

## Deploy the public demo to Vercel

The public demo is intentionally separated from the local process manager inside the `vercel-demo/` folder. It uses a small Python Flask serverless function with `requirements.txt` and `vercel.json`. The demo supports `admin` / `admin` login, simulated project status, simulated terminal logs, profiles, and safe start/stop interactions. It never runs `.bat`, `.exe`, Cloudflare, firewall, or other local commands.

To deploy it from GitHub:

1. Import this repository into Vercel.
2. Set **Root Directory** to `vercel-demo`.
3. Use the **Other** framework preset, or let Vercel detect the Python runtime from `requirements.txt`.
4. Leave the build command and output directory empty.
5. Deploy and assign `welllauncher.vercel.app` as the production domain.
6. Open the deployment and sign in with `admin` / `admin`.

The local launcher remains at the repository root and should continue to run on your own computer. The Vercel deployment is only a safe public showcase of the interface; it cannot control your local projects or infrastructure.

## Repository structure

```text
.
├── server.py
├── launcher_config.json
├── requirements.txt
├── README.md
├── .gitattributes
├── .gitignore
├── robots.txt
├── sitemap.xml
├── og-image.png
├── og-image.svg
├── vercel-demo/
│   ├── api/index.py
│   ├── index.html
│   ├── requirements.txt
│   └── vercel.json
├── WELL_Launcher_Guide.txt
├── -- INSTALL --.bat
├── -- RESET_PASSWORD --.bat
├── -- START_WELL_LAUNCHER --.bat
└── static/
    ├── index.html
    ├── favicon.ico
    ├── og-image.png
    └── og-image.svg
```

The `pids/` folder and `users.json` file are created at runtime and do not need to be committed.

## Main features

- Start or stop one project or all projects in the active profile.
- `ONLINE`, `STARTING`, and `OFFLINE` status badges.
- Real-time terminal logs with copy, clear, bottom, fullscreen, and split-view actions.
- Automatic scrolling to the newest log line when a panel is opened or new output arrives.
- Profiles for grouping projects.
- Port scanner.
- User roles: admin and viewer.
- Access from another device on the same LAN through `http://COMPUTER-IP:9000` when the firewall allows it.

## Adding your own projects

Projects can be added from the **Projects** page or by editing `launcher_config.json`. Important fields include:

- `name`: project name shown in the dashboard.
- `cwd`: project working directory.
- `cmd`: command used to start the project.
- `port`: application port, if applicable.
- `domain`: optional domain displayed by the launcher.
- `is_background`: mark the project as a background service.
- `enabled`: show or hide the project.

For Python projects using a virtual environment, call the Python executable inside the virtual environment directly. For example:

```bat
C:\path\to\venv\Scripts\python.exe main.py
```

## Password and account reset

Use **Change Password** in the dashboard to change your own password. If the admin password is forgotten, run:

```bat
-- RESET_PASSWORD --.bat
```

Passwords are stored as PBKDF2 hashes by the backend. Never add `users.json` to GitHub.

## Git workflow

After cloning and configuring your projects:

```bash
git status
git add .
git commit -m "Update launcher configuration"
git push
```

To create a new repository from this folder:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

## SEO and public metadata

The public site metadata is configured for `https://welllauncher.vercel.app/` and includes:

- Search title and description for WELL Launcher.
- Author metadata for **Samuel Extehines Heydemans**.
- GitHub profile reference: [github.com/samwhine](https://github.com/samwhine).
- Canonical URL, Open Graph tags, and Twitter/X summary card metadata.
- JSON-LD structured data for the author, website, and software application.
- `robots.txt`, `sitemap.xml`, and a 1200×630 social preview image at `/og-image.png`.

Metadata improves discoverability but does not guarantee an immediate Google result. Deploy the site publicly, verify the domain in [Google Search Console](https://search.google.com/search-console), submit `https://welllauncher.vercel.app/sitemap.xml`, and allow time for Google to crawl and index the site.

## Security notes

WELL Launcher is intended for local use or trusted networks. Do not expose it directly to the public internet without an appropriate authentication layer, HTTPS, firewall rules, and deployment configuration. Never commit `users.json`, `.env`, API keys, tokens, private domains, or private project configuration.
