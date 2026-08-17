# ERA X BOT — Railway / Render Deployment Package

This package contains the production entrypoint and deployment configuration for **ERA X BOT**, the Telegram bot that automatically detects APK files, ZIP archives, Firebase configuration, and panel links. Normal users receive generated panel links without seeing Firebase URLs or API keys. Administrative features remain protected by `ADMIN_IDS`.

## Package contents

| File or directory | Purpose |
|---|---|
| `bot.py` | Complete ERA X BOT source code. |
| `main.py` | Deployment entrypoint used by Railway, Render, Docker, and local execution. |
| `requirements.txt` | Python dependencies. |
| `Procfile` | Worker command for platforms that read Procfile definitions. |
| `railway.toml` | Railway build and restart configuration. |
| `render.yaml` | Render background-worker configuration. |
| `Dockerfile` | Portable container deployment definition. |
| `.env.example` | Safe environment-variable template containing no secrets. |
| `.gitignore` | Prevents credentials, runtime records, caches, and logs from being committed. |
| `data/` | Empty runtime directories with `.gitkeep` files. |

## Required environment variables

Create these variables in the hosting provider’s secret/environment settings. Do not put real values into a public Git repository or into the ZIP before sharing it.

| Variable | Required value |
|---|---|
| `BOT_TOKEN` | The Telegram bot token issued by BotFather. |
| `ADMIN_IDS` | Telegram admin IDs separated by commas, for example `123456789,987654321`. |
| `FIREBASE_DATABASE_URL` | The Firebase Realtime Database URL ending in `firebaseio.com` or `firebasedatabase.app`. |

The Firebase Web SDK `apiKey` is not required by this bot’s server-side synchronization code. The bot uses the Realtime Database URL over HTTPS.

## Railway deployment

Create a new Railway project from this package or from its Git repository. Railway should detect the Python project automatically; `railway.toml` and `Procfile` both define `python main.py` as the worker command. Add `BOT_TOKEN`, `ADMIN_IDS`, and `FIREBASE_DATABASE_URL` under the service variables, then deploy and inspect the deployment logs for the messages `Firebase DB reachable` and `Application started`.

Use one worker instance only unless the bot is deliberately redesigned for webhook-based multi-instance operation. Telegram polling must not run simultaneously in multiple replicas because duplicate updates can be processed.

## Render deployment

Create a Render Background Worker from the repository or use the included `render.yaml`. Set the three environment variables as secret values and deploy. The worker command is `python main.py`. A background worker is the appropriate service type for the current long-polling implementation; it does not require a public HTTP endpoint.

## Docker deployment

Build and run the container with:

```bash
docker build -t era-x-bot .
docker run --env-file .env era-x-bot
```

The `.env` file must be created locally from `.env.example` and must never be uploaded to a public repository.

## Local verification

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and insert the real values securely.
python main.py
```

A healthy startup should report that the Firebase database is reachable and that the Telegram application has started. Automatic APK, media, and panel-link detection is intentionally enabled only in private chats; ordinary group and channel messages are ignored. Explicit commands such as `/start`, `/help`, and admin commands remain available according to Telegram permissions and bot access. Then open **Panel** to confirm that only the user’s generated panel URLs are displayed.

## Data persistence and privacy

The bot writes local JSON backups under `data/`, but ephemeral hosting filesystems may be cleared during redeployments or restarts. Firebase Realtime Database should therefore remain configured as the durable source of synchronized user, scan, panel, and administrative state. The deployment archive intentionally excludes the current local JSON records and the existing `.env` file so that user data and credentials are not distributed to the admin or committed accidentally.

Before production deployment, rotate any bot token or Firebase credential that has previously been pasted into a chat, repository, log, screenshot, or shared archive. Store all secrets only in the hosting provider’s encrypted environment-variable settings.

## Troubleshooting

If the bot starts but does not respond, verify that `BOT_TOKEN` is valid and that only one deployment instance is polling. If Firebase is unreachable, verify `FIREBASE_DATABASE_URL`, the Realtime Database rules, and outbound network access. If panel links show an unexpected domain, confirm that the deployed `bot.py` is the latest package version and redeploy after changing the configured panel URL.

## Deployment limitation

This archive is ready to deploy. I can prepare and validate the package here, but I cannot independently complete the final Railway or Render account actions unless you provide an authorized, active browser session or a properly enabled deployment connector. The final steps involve your account, secret variables, and deployment confirmation. If you connect the relevant account when prompted, I can guide or carry out the remaining non-sensitive deployment steps; you may also provide the required values through the hosting platform’s secure variable form rather than sending them in chat.
