# Concours Bot 🇲🇦

A Telegram bot that watches ~240 Moroccan school, university and orientation websites
24/7 and sends a message as soon as a new **concours / inscription / résultats / مباراة**
announcement appears.

## What it does

- Checks every site every 30 minutes (configurable), 8 sites in parallel.
- Reads the RSS feed when a site has one, otherwise reads the page's links.
- Keeps only links that look like announcements (French + Arabic keywords, school acronyms).
- **Never sends twice.** Everything is remembered in a small SQLite database.
- **First run is silent.** It saves what's already online so you don't get 500 old alerts.
- **Flood protection.** If a site suddenly shows lots of new links (redesign), they're saved
  silently and you get one short note instead.
- **Health monitoring.** Dead sites never spam the channel. You get a daily report and can
  ask `/failing` anytime.
- Admin commands in Telegram: `/status`, `/check`, `/last`, `/failing`, `/pause`…
- Sites are edited in `sites.yaml` and changes apply on the next check (no restart needed).

## 1. Create the Telegram bot (5 min)

1. In Telegram, open **@BotFather** → `/newbot` → pick a name → copy the **token**.
2. Open your new bot and press **Start**.
3. Get your user id: leave `CHAT_ID` empty in `.env` and start the bot. It runs in
   **setup mode** and replies to `/start` with your id. Put that id in `CHAT_ID` and restart.

**Where should announcements go?**

- **Private chat with the bot (simplest).** `CHAT_ID` = your user id.
- **A channel (recommended for a content creator).** Create a channel (private is fine) and add
  the bot as **administrator** with "Post messages". `CHAT_ID` = `@channelname`, or the numeric
  `-100…` id for a private channel. Set `ADMIN_ID` = your user id so commands and reports come
  to you privately.

### Sharing with a friend (nothing to install for him)

The bot runs in **one** place (ideally a server). Everyone else only needs Telegram:

1. Your friend opens the bot in Telegram and presses **Start**. The bot replies with his id.
2. Add his id to `.env`, separated by a comma:
   ```
   CHAT_ID=111111111,222222222     # you, your friend
   ADMIN_ID=111111111,222222222    # both can use /search, /latest, /status…
   ```
3. Restart the bot. Send `/test` to check that both of you receive it.

Never run the same bot token in two places at once; Telegram only allows one.

## 2. Configure

```bash
cp .env.example .env
nano .env        # put BOT_TOKEN, CHAT_ID, ADMIN_ID
```

## 3. Run

### Option A: Docker (recommended on a server)

```bash
docker compose up -d --build
docker compose logs -f          # watch it work
```

It restarts automatically after crashes and reboots. Data lives in `./data/`.

### Option A on Windows: Docker Desktop

1. Install **Docker Desktop** from <https://www.docker.com/products/docker-desktop/> and open it
   (wait until it says "Engine running").
2. Open **PowerShell** in the `concours-bot` folder (in File Explorer: click the address bar,
   type `powershell`, press Enter).
3. Run:

```powershell
docker compose up -d --build
docker compose logs -f
```

Stop with `docker compose down`. Keep in mind a PC isn't on 24/7: use a server for the real thing.

### Option B: Plain Python

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py --once      # one check, then exit (good first test)
python main.py             # run forever
```

To keep it running without Docker, use the included `concours-bot.service` (systemd).

### Test without Telegram

```bash
DRY_RUN=1 python main.py --once     # prints messages instead of sending
python -m tests.test_end_to_end     # automated test with fake websites
```

## 4. Free hosting on GitHub (no server, no card)

GitHub runs the bot every ~30 minutes for free (public repository). All filters run the same.
Difference: commands like `/search` are answered at the next run (up to ~30 min later), and
GitHub sometimes starts runs a few minutes late.

1. Create a free account on <https://github.com> and a **New repository** named `concours-bot`,
   **Public**, without README.
2. On the empty repo page click **uploading an existing file**, drag in **all the files and
   folders** of the project, and click **Commit changes**.
   **Never upload `.env`**: the repository is public and `.env` contains the token.
3. If the `.github` folder didn't upload (Windows sometimes skips it): **Add file > Create new
   file**, name it `.github/workflows/bot.yml`, paste the content of that file, commit.
4. **Settings > Secrets and variables > Actions > New repository secret**, add:
   - `BOT_TOKEN`: the token from BotFather
   - `CHAT_ID`: your id (and your friend's, comma separated)
   - `ADMIN_ID`: same as CHAT_ID
5. **Actions** tab > enable workflows > **Concours bot** > **Run workflow**. After about
   2 minutes you get "Bot is now running on GitHub" in Telegram.

Stop any other copy of the bot first (`docker compose down` on your PC): one token, one bot.

The bot's memory is saved in a branch called `state`. Logs of each run: **Actions** tab.
If you edit `sites.yaml` on GitHub, the next run uses it.
If a public repo has no new commit for 60 days, GitHub may pause scheduled runs; it emails you
first, and you re-enable it in the Actions tab with one click.

## 5. Hosting it 24/7 on a server (cheap)

| Option | Cost | Notes |
|---|---|---|
| **Oracle Cloud Always Free** | 0 € | Ubuntu ARM VM. Plenty for this bot. Sometimes "out of capacity" when creating it; try another region or later. |
| **Hetzner Cloud** (smallest CX plan) | ~4 €/month | Most reliable cheap option. Recommended for the paid version. |

Setup on any Ubuntu server:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
# copy the project folder to the server (scp, git, or unzip), then:
cd concours-bot
cp .env.example .env && nano .env
docker compose up -d --build
```

**Optional uptime alert:** create a free check on <https://healthchecks.io> with a 1-hour
period, put its ping URL in `HEALTHCHECK_URL`, and you'll get an email if the bot stops.

## 6. Recommended first step: clean the site list

Many URLs in the original list are old. Run the checker once from the server:

```bash
docker compose run --rm concours-bot python check_sites.py
# or: python check_sites.py
```

It writes `data/site_report.csv` (opens in Excel) with each site's status:

- **OK**: works.
- **DOWN**: dead, wrong domain or timeout. Fix the URL or set `enabled: false`.
- **NO_LINKS**: site answers but no announcement links were found. Look at it with
  `python check_sites.py <site_id>`, then add a `selector` or `rss` (see below).
- **final_url**: the site redirects. Update the URL to the new address.

## 7. Editing `sites.yaml`

```yaml
- id: ensa_agadir                 # unique, used in /site ensa_agadir
  name: ENSA Agadir               # shown in messages
  url: http://www.ensa-agadir.ac.ma/
  category: ensa                  # used for the emoji + hashtag
  # optional:
  enabled: false                  # skip this site
  rss: https://site/feed/         # use a feed (most reliable)
  selector: ".actualites a"       # only read links inside this block of the page
  keywords: false                 # send every new link, not only keyword matches
```

**Finding a selector:** open the site in Chrome, right-click the news list → *Inspect*, and
look at the class of the block around the announcements (e.g. `<div class="news-list">` gives
`.news-list a`). Check with `python check_sites.py <id>`.

Entries that pointed to the same URL in the original list (e.g. all CMC → myway.ac.ma) were
merged into one site. The other names are kept under `also:`, so 279 entries became 244 sites.

To regenerate from a new JSON list: `python tools/import_list.py list.txt > sites.yaml`.

## 8. Telegram commands (admin only)

| Command | What it does |
|---|---|
| `/search <words>` | Search everything the bot has found: `/search ENSA`, `/search مباراة الطب` |
| `/latest` | Tap a main site (Tawjihnet, 9rayti…) to see its last 10 announcements |
| `/latest <site>` | Same for any site: `/latest encg settat`, `/latest fmp` |
| `/status` | Running/paused, last check result, next check |
| `/check` | Check all sites now |
| `/last` | Last 10 announcements sent |
| `/failing` | Unreachable sites with the error |
| `/empty` | Reachable sites where no announcement links were found |
| `/site <id>` | Details for one site |
| `/report` | Health report now (sent automatically every day at `REPORT_HOUR`) |
| `/pause` / `/resume` | Stop / restart checking |
| `/test` | Send a test message to `CHAT_ID` |
| `/stats` | Counters |

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | required | from @BotFather |
| `CHAT_ID` | required | where announcements go (comma-separated for several) |
| `ADMIN_ID` | = CHAT_ID | who can use commands, gets reports (comma-separated) |
| `INTERVAL_MINUTES` | 30 | time between checks |
| `REPORT_HOUR` | 9 | daily report hour (-1 = off) |
| `MAX_WORKERS` | 8 | parallel downloads |
| `MAX_NEW_PER_SITE` | 15 | flood protection threshold |
| `LINK_PREVIEW` | true | link preview under messages |
| `EXTRA_KEYWORDS` | | extra words to match, comma separated |
| `HEALTHCHECK_URL` | | healthchecks.io ping URL |
| `DRY_RUN` | 0 | print instead of sending |

## Project layout

```
main.py              entry point (loop + Telegram commands thread)
check_sites.py       one-shot site checker -> data/site_report.csv
sites.yaml           the watched sites
bot/config.py        settings + site list loading
bot/fetcher.py       HTTP (timeouts, bad SSL certificates, size limit)
bot/extractor.py     links / RSS -> announcements, keyword matching
bot/storage.py       SQLite (seen links, site health)
bot/monitor.py       check cycle, message format, reports
bot/commands.py      admin commands
bot/telegram.py      Telegram API client (rate limited, retries)
tests/               end-to-end test with fake websites
```

## Troubleshooting

- **Bot doesn't post in the channel**: it must be channel *admin*. Run `/test`.
- **Commands don't answer**: `ADMIN_ID` must be your *user* id (send `/start` to see it).
- **Many sites DOWN at once**: the server's internet connection, or the sites block foreign
  IPs. Check `/failing`. Some Moroccan government sites block traffic from outside Morocco.
  If many `.gov.ma` sites fail from an EU server but open fine on your phone, that's the reason.
- **Logs**: `docker compose logs -f` or `data/bot.log`.
- **Start fresh**: stop the bot and delete `data/bot.db`. The next run baselines silently again.
