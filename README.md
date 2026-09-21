# WELL Launcher

WELL Launcher adalah dashboard lokal untuk menjalankan, menghentikan, dan memantau beberapa project melalui satu antarmuka web. Dashboard menyediakan status process, terminal log real-time, split log, profiles, port scanner, serta user roles.

Repository ini menyertakan **konfigurasi demo yang aman** agar orang lain dapat menjalankan dan mencoba dashboard setelah clone tanpa membutuhkan project pribadi.

## Demo login

Pada clone baru, `users.json` belum ada dan akan dibuat otomatis oleh `server.py` dengan akun default:

| Username | Password | Role |
|---|---|---|
| `admin` | `admin` | Admin |

Segera ganti password dari menu **Change Password** sebelum memakai launcher untuk project sungguhan. `users.json` sengaja masuk `.gitignore` karena file tersebut berisi password hash dan data login.

## Requirements

- Windows 10/11 untuk menjalankan file `.bat` dan process project Windows.
- Python 3.10 atau lebih baru.
- Python packages: `Flask` dan `requests`.

## Quick start di Windows

1. Clone repository:

   ```bat
   git clone https://github.com/USERNAME/REPOSITORY.git
   cd REPOSITORY
   ```

2. Jalankan installer:

   ```bat
   -- INSTALL --.bat
   ```

3. Jalankan launcher:

   ```bat
   -- START_WELL_LAUNCHER --.bat
   ```

4. Buka [http://localhost:9000](http://localhost:9000) jika browser tidak terbuka otomatis.

5. Login menggunakan `admin` / `admin`, lalu ganti password.

Installer menjalankan `pip install flask requests`. Jika ingin memasangnya manual:

```bat
python -m pip install flask requests
python server.py
```

## Demo projects

`launcher_config.json` berisi project demo yang tidak bergantung pada folder pribadi:

- **Demo HTTP Server** — menjalankan `python -m http.server 8765`.
- **Demo Log Stream** — menghasilkan output terminal berkala untuk mencoba auto-scroll dan split log.
- **Demo Background Service** — menjalankan HTTP server kedua di port 8766 dan menjadi contoh background service.

Konfigurasi demo ini memang di-commit agar fresh clone dapat langsung dipakai untuk live test. Untuk memakai project pribadi, edit `launcher_config.json` setelah clone. Jangan commit path pribadi, domain internal, token, API key, atau credential ke repository publik.

## Struktur repository

```text
.
├── server.py
├── launcher_config.json
├── README.md
├── WELL_Launcher_Guide.txt
├── -- INSTALL --.bat
├── -- RESET_PASSWORD --.bat
├── -- START_WELL_LAUNCHER --.bat
└── static/
    ├── index.html
    └── favicon.ico
```

Folder `pids/` dan file `users.json` dibuat saat runtime sehingga tidak perlu di-commit.

## Fitur utama

- Start/stop satu project atau seluruh project dalam profile aktif.
- Status `ONLINE`, `STARTING`, dan `OFFLINE`.
- Terminal log real-time dengan copy, clear, bottom, fullscreen, dan split view.
- Auto-scroll ke log terbaru saat panel baru dibuka atau output baru masuk.
- Profiles untuk mengelompokkan project.
- Port scanner.
- User roles: admin dan viewer.
- Akses dari device lain di LAN melalui `http://IP-KOMPUTER:9000` jika firewall mengizinkan.

## Menambahkan project sendiri

Project dapat ditambahkan melalui menu **Projects** atau dengan mengedit `launcher_config.json`. Field penting yang tersedia antara lain:

- `name`: nama project yang tampil di dashboard.
- `cwd`: working directory project.
- `cmd`: command untuk menjalankan project.
- `port`: port aplikasi, jika ada.
- `domain`: domain opsional yang ditampilkan launcher.
- `is_background`: tandai sebagai background service.
- `enabled`: tampilkan atau sembunyikan project.

Untuk command Python dengan virtual environment, gunakan executable Python dari virtual environment secara langsung. Contoh:

```bat
C:\path\ke\venv\Scripts\python.exe main.py
```

## Password dan reset account

Gunakan **Change Password** dari dashboard untuk mengganti password sendiri. Jika password admin terlupa, jalankan:

```bat
-- RESET_PASSWORD --.bat
```

Password disimpan sebagai hash PBKDF2 oleh backend. Jangan menambahkan `users.json` ke GitHub.

## Git workflow

Setelah clone dan pengaturan project selesai:

```bash
git status
git add .
git commit -m "Update launcher configuration"
git push
```

Untuk membuat repository baru dari folder ini:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

## Catatan keamanan

WELL Launcher dirancang untuk penggunaan lokal atau jaringan tepercaya. Jangan expose langsung ke internet tanpa authentication layer, HTTPS, firewall rules, dan konfigurasi deployment yang sesuai. Jangan commit `users.json`, `.env`, API keys, token, private domain, atau konfigurasi project pribadi.
