# Price Tracker

Track and compare product prices across Indonesian e-commerce marketplaces: **Blibli**, **Shopee**, **Tokopedia**, and **Lazada**.

## Features

- **Multi-marketplace tracking** — Track one product (SKU) across 4 marketplaces simultaneously.
- **Price history** — Store all price changes in SQLite with full metadata.
- **Price comparison** — Side-by-side comparison table with the lowest price highlighted.
- **Chart generation** — Minimalist PNG charts with annotated highs and lows.
- **Fake flash sale detection** — Detect when a "discount" is just an inflated original price.
- **Telegram alerts** — Get notified when prices drop below your threshold.

## Installation

```bash
# Clone the repo
git clone <your-repo-url>
cd price-tracker

# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Linux/macOS)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (required for Shopee, Tokopedia, Lazada)
playwright install chromium

# Copy config template
cp config.example.toml config.toml
```

## Configuration

Edit `config.toml` with your settings:

```toml
[telegram]
bot_token = "YOUR_BOT_TOKEN"
chat_id = "YOUR_CHAT_ID"

[scraper.browser]
headless = true  # set to true for server/CI environments
```

Or use environment variables:
```bash
export TELEGRAM_BOT_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
```

## Telegram Bot Setup

1. Open Telegram, search for **@BotFather**.
2. Send `/newbot`, follow the prompts to create a bot.
3. Copy the bot token.
4. Run the setup script:

```bash
python scripts/setup_telegram.py
```

## Usage

### Initialize database & seed sample data

```bash
# Seed with 3 sample products + 30 days of synthetic price history
python scripts/seed_products.py
```

### Add a product to tracking

```bash
python main.py add https://www.blibli.com/p/samsung-galaxy-s24-ultra/ps--SAM-70001-00001
python main.py add https://shopee.co.id/Product-Name-i.12345.67890
python main.py add https://www.tokopedia.com/officialstore/product-name
python main.py add https://www.lazada.co.id/products/product-name-i123456789-s987654321.html
```

### Track all products (scrape + update + alert)

```bash
python main.py track
```

### Compare prices across marketplaces

```bash
python main.py compare ps--SAM-70001-00001
```

Example output:
```
┌──────────────────────────────────────────────────────────┐
│          Price Comparison: ps--SAM-70001-00001            │
├─────────────┬───────────────────┬──────────────┬─────────┤
│ Marketplace │    Current Price  │ Original     │Discount │
├─────────────┼───────────────────┼──────────────┼─────────┤
│ blibli      │ Rp 18,999,000 ✓  │ Rp 21,999,000│  14%    │
│ shopee      │ Rp 19,250,000    │ Rp 22,000,000│  13%    │
│ tokopedia   │ Rp 19,499,000    │ Rp 21,999,000│  11%    │
│ lazada      │ Rp 19,999,000    │ Rp 24,999,000│  20%    │
└─────────────┴───────────────────┴──────────────┴─────────┘
```

### Generate price chart

```bash
# Specific marketplace
python main.py chart ps--SAM-70001-00001 --marketplace blibli --days 30

# All marketplaces with data
python main.py chart ps--SAM-70001-00001
```

### Check for fake flash sales

```bash
python main.py check-fake ps--SAM-70001-00001 --marketplace blibli
```

### Test Telegram connection

```bash
python main.py alert-test
```

### Self-Diagnostic Health Check (Doctor)

Run the automated self-diagnostic tool to check pipeline health:

```bash
# Run all checks (environment, database, cookies, scrapers, alerts, chart, scheduler, logs)
python doctor.py

# Run specific sub-check
python doctor.py env
python doctor.py db
python doctor.py cookies
python doctor.py scrapers
python doctor.py alert
python doctor.py chart
python doctor.py scheduler
python doctor.py logs

# Override Task Scheduler task name explicitly
python doctor.py scheduler --task-name "Price Tracker"

# Output pure JSON for CI/automation
python doctor.py --json
```

## Task Scheduler Setup

Price Tracker dapat dijadwalkan berjalan otomatis (misalnya setiap 6 jam) di Windows menggunakan Task Scheduler.

### 1. Setup Otomatis via `doctor.py fix-scheduler`

Gunakan subcommand `doctor.py fix-scheduler` untuk mendaftarkan task secara otomatis:

**Mode Interaktif (Default):**
```bash
python doctor.py fix-scheduler
```
Skrip akan memandu Anda memasukkan nama task (default: `Price Tracker`), jam mulai (default: `08:00`), interval eksekusi (default: `6` jam), dan path `run_track.bat`, kemudian menampilkan preview perintah sebelum meminta konfirmasi.

**Mode Non-Interaktif (Flag Lengkap):**
```bash
python doctor.py fix-scheduler --name "Price Tracker" --start "08:00" --interval 6 --batch "C:\Users\Lol\Downloads\Project\Price tracker e-commerce\price-tracker\run_track.bat" --yes
```

### 2. Setup Manual via Task Scheduler GUI (`taskschd.msc`)

Jika setup otomatis mengalami kendala izin atau kebijakan sistem, Anda dapat membuatnya secara manual melalui GUI:

1. Tekan `Win + R`, ketik `taskschd.msc`, lalu tekan **Enter**.
2. Di panel kanan (Actions), klik **Create Task...** (bukan Create Basic Task).
3. **Tab General**:
   - Berikan nama task: `Price Tracker`.
   - Konfigurasikan opsi keamanan akun sesuai preferensi (*Run only when user is logged on* atau *Run whether user is logged on or not*).
4. **Tab Triggers**:
   - Klik **New...** → Begin the task: *On a schedule*.
   - Pilih *Daily*, Start: `08:00:00`.
   - Centang *Repeat task every:* `6 hours` for a duration of: `1 day` (versi bahasa Indonesia: *Ulangi tugas setiap 6 jam* selama *1 hari*).
   - Pastikan *Enabled* dicentang.
5. **Tab Actions**:
   - Klik **New...** → Action: *Start a program*.
   - Program/script: `C:\Users\Lol\Downloads\Project\Price tracker e-commerce\price-tracker\run_track.bat`
   - **Start in (optional)**: Masukkan direktori root project: `C:\Users\Lol\Downloads\Project\Price tracker e-commerce\price-tracker` (Sangat penting agar path relatif database dan file log dapat ditemukan).
6. Klik **OK** untuk menyimpan task.

### 3. Mengganti Nama Task

Jika Anda sudah mendaftarkan task dengan nama kustom selain `Price Tracker`:
1. Buka file `config.toml`.
2. Tentukan nama task pada section `[scheduler]`:
   ```toml
   [scheduler]
   task_name = "Nama Task Anda"
   ```
3. Atau jalankan doctor dengan override langsung via CLI:
   ```bash
   python doctor.py scheduler --task-name "Nama Task Anda"
   ```

### 4. Verifikasi Task Scheduler

Gunakan `doctor.py` untuk memverifikasi apakah task terdaftar dan aktif:

```bash
# Verifikasi otomatis (auto-detect kandidat & cek config.toml)
python doctor.py scheduler

# Verifikasi dengan nama spesifik
python doctor.py scheduler --task-name "Price Tracker"

# Output format JSON
python doctor.py scheduler --json
```

### 5. Troubleshooting Task Scheduler

- **`Access is denied` / `Akses ditolak`**:
  Perintah pendaftaran task membutuhkan hak akses administrator. Buka Command Prompt atau PowerShell dengan klik kanan lalu pilih **Run as Administrator**, kemudian jalankan kembali `python doctor.py fix-scheduler`.
- **`The task already exists` / `Tugas sudah ada`**:
  Gunakan flag `--overwrite` saat menjalankan command, atau konfirmasi timpa saat muncul prompt interaktif:
  ```bash
  python doctor.py fix-scheduler --name "Price Tracker" --overwrite --yes
  ```
- **Task Terdaftar tapi Tidak Berjalan**:
  1. Periksa direktori kerja ("Start In"): Task Scheduler memerlukan folder root project agar script Python dan file log dapat diakses.
  2. Periksa detail status dan hasil eksekusi terakhir:
     ```cmd
     schtasks /query /tn "Price Tracker" /fo LIST /v
     ```
  3. Periksa catatan log eksekusi pada `data/logs/track.log`.

## Running Tests

```bash
pytest tests/ -v
```

## Marketplace Notes

### Blibli (Easiest)
- **Method**: Plain HTTP requests + BeautifulSoup.
- **Anti-bot**: Minimal. No Cloudflare, no heavy JS rendering.
- **Rate limit**: 1 request per 2 seconds is safe.
- **Reliability**: Most stable scraper — selectors change rarely.

### Lazada (Playwright Required)
- **Method**: Playwright (headless browser).
- **Anti-bot**: Cloudflare challenge on first visit. Stealth mode usually passes it.
- **Rate limit**: Minimum 3 seconds between page loads.
- **Known issue**: Cloudflare may require manual intervention if challenge persists.

### Tokopedia (GraphQL + Fallback)
- **Method**: GraphQL API (primary) + Playwright (fallback).
- **Anti-bot**: Custom bot detection, not Cloudflare.
- **GraphQL hash**: The `operation_hash` in config.toml changes when Tokopedia updates their frontend. To update:
  1. Open a product page in Chrome.
  2. Open DevTools → Network tab → filter by "graphql".
  3. Find the `PDPGetLayoutQuery` request.
  4. Copy the `sha256Hash` from the request payload.
  5. Update `config.toml` → `[scraper.tokopedia]` → `operation_hash`.
- If the hash is empty or invalid, the scraper falls back to Playwright.

### Shopee (API + Fallback)
- **Method**: Internal API v4 (primary) + Playwright (fallback).
- **Anti-bot**: Aggressive. API returns 403 frequently. Browser may show captcha.
- **API endpoint**: `https://shopee.co.id/api/v4/product/get` — last major change was in 2024 (v2 → v4). Verify every 3 months.
- **Rate limit**: Minimum 3 seconds between requests (conservative).
- **Price format**: API returns prices in micro-units (price × 100,000).

### Selector Verification Schedule
All CSS selectors and API endpoints should be verified every **3 months**.
Current verification date: **2026-09-26**.
Next due: **2027-01-01**.

## Maintenance Notes

### 1. Re-export Cookie Lazada (Jika Session Expired)
Cookie session Lazada berfungsi sebagai kredensial autentikasi sesi untuk melewati tantangan Cloudflare & reCAPTCHA:
1. Buka browser Chrome/Edge secara manual (bukan via Playwright headless).
2. Kunjungi [https://www.lazada.co.id](https://www.lazada.co.id) dan selesaikan verifikasi CAPTCHA jika muncul hingga halaman dapat dibuka dengan normal.
3. Gunakan browser extension seperti **Cookie-Editor** (tersedia di Chrome Web Store / Firefox Add-ons).
4. Klik icon Cookie-Editor, pilih menu **Export**, lalu pilih format **JSON**.
5. Simpan / replace file di `input/lazada_cookies.json` (format berupa JSON array).
6. File cookie ini otomatis masuk dalam `.gitignore` — jangan pernah meng-commit file kredensial ini ke repository git.

### 2. Update Operation Hash Tokopedia dari DevTools Network Tab
Tokopedia menggunakan endpoint GraphQL yang memerlukan query operation hash (`sha256Hash`) yang valid:
1. Buka salah satu halaman detail produk Tokopedia di browser.
2. Buka **DevTools** (`F12` atau klik kanan -> *Inspect*), lalu buka tab **Network**.
3. Filter request dengan kata kunci `graphql` atau `ProductQuery`.
4. Klik request tersebut, buka tab **Payload** / **Request Body**.
5. Cari dan salin nilai dari `extensions.persistedQuery.sha256Hash` (misal pada query `PDPGetLayoutQuery`).
6. Masukkan hash tersebut ke file `config.toml`:
   ```toml
   [scraper.tokopedia]
   operation_hash = "<paste_hash_di_sini>"
   ```
   *Catatan*: Jika `operation_hash` masih kosong saat scraper Tokopedia dipanggil, sistem akan langsung me-raise `ScraperError` dengan pesan instruksi yang jelas.

### 3. Checklist Verifikasi Selector Blibli (Tiap 3 Bulan)
Blibli merender halaman via React SPA dan scraper melakukan intersepsi pada internal API. Lakukan checklist berkala setiap 3 bulan:
- [ ] Buka halaman produk Blibli di browser dengan DevTools Network tab aktif.
- [ ] Pastikan endpoint price summary API masih cocok dengan pola `/backend/product-detail/products/`.
- [ ] Pastikan parsing JSON response untuk harga (`price`), harga coret (`originalPrice`), dan stok (`stock`) masih valid.
- [ ] Perbarui catatan tanggal verifikasi di docstring `src/scrapers/blibli.py`:
  - Terakhir diverifikasi: `2026-09-26`
  - Jadwal verifikasi berikutnya: `2026-12-26` (setiap 3 bulan)

## Troubleshooting

### `playwright._impl._errors.Error: Executable doesn't exist`
```bash
playwright install chromium
```

### `ModuleNotFoundError: No module named 'tomli'`
```bash
pip install tomli  # only needed for Python < 3.11
```

### Scraper returns "Forbidden (403)"
- **Blibli**: Rare. Check if your IP is blocked (try VPN).
- **Shopee/Tokopedia**: Anti-bot triggered. Wait 5-10 minutes, then retry. Consider using `headless = false` in config for debugging.
- **Lazada**: Cloudflare challenge. Try running with `headless = false` to manually solve the challenge once.

### Charts look empty / "Not enough data"
You need at least 2 data points to generate a chart. Run `python main.py track` multiple times over different days, or seed the database first:
```bash
python scripts/seed_products.py
```

### Telegram alert not sending
1. Run `python main.py alert-test` to verify the connection.
2. Check if `bot_token` and `chat_id` are set in `config.toml` or environment variables.
3. Rate limit: max 1 alert per SKU per hour. Check `data/logs/alerts.log`.

### Database errors after update
```bash
python scripts/migrate_db.py
```

## Project Structure

```
price-tracker/
├── main.py                 # CLI entry point
├── config.example.toml     # Config template
├── requirements.txt        # Python dependencies
├── src/
│   ├── scrapers/           # Marketplace-specific scrapers
│   │   ├── base.py         # Abstract base class
│   │   ├── http_base.py    # HTTP + BeautifulSoup base
│   │   ├── browser_base.py # Playwright + stealth base
│   │   ├── blibli.py       # Blibli (HTTP)
│   │   ├── lazada.py       # Lazada (Playwright)
│   │   ├── tokopedia.py    # Tokopedia (GraphQL + Playwright)
│   │   └── shopee.py       # Shopee (API + Playwright)
│   ├── db.py               # SQLite database layer
│   ├── models.py           # Data models (dataclasses)
│   ├── detector.py         # Fake flash sale detection
│   ├── chart.py            # Price chart generation
│   ├── alerts.py           # Telegram notifications
│   ├── config.py           # Config loader (TOML)
│   └── utils.py            # Parsing, retry, logging
├── scripts/
│   ├── setup_telegram.py   # Interactive Telegram setup
│   ├── migrate_db.py       # Database migrations
│   └── seed_products.py    # Sample data seeder
├── tests/                  # Unit tests
├── data/                   # Runtime data (gitignored)
└── .github/workflows/      # CI/CD
```

## License

MIT
