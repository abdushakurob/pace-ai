# Pace

**Pace turns an intention into a calendar block you can follow through on.**

Write what needs to happen in plain language. Pace reads the commitments you connected, asks your chosen model to interpret the request, finds a valid opening, and shows you a proposal. Nothing is written to your calendar until you approve it.

Pace has two pages:

- `/` is the product page.
- `/app` is your private workspace.

## Use Pace from scratch

1. Open `/app` and go to **Connections**.
2. Connect **Google Calendar**, then select **Sync today**.
3. Go to **Settings** and choose an AI model: Arduino Uno Q, a local OpenAI-compatible model server, or a hosted compatible model.
4. Return to **Today** and select **Tell Pace**.
5. Write a request such as: `I have an exam tomorrow morning. Find two hours to study.`
6. Review Pace's proposed time. Select **Use this plan** only when it looks right.
7. Open **Devices** to pair your phone using the QR code or private link.

Pace reads calendar commitments and writes only the focus blocks you explicitly approve.

## Run Pace locally

Install Python 3.12 or newer. From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe pace-core\python\hub.py
```

If your environment uses `bin`, replace `Scripts` with `bin`.

Open [http://127.0.0.1:7462](http://127.0.0.1:7462) for the product page or [http://127.0.0.1:7462/app](http://127.0.0.1:7462/app) for the workspace.

### Use from another device on your Wi-Fi

```powershell
$env:PACE_HOST = "0.0.0.0"
$env:PACE_OAUTH_CALLBACK_BASE = "http://127.0.0.1:7462"
.\.venv\Scripts\python.exe pace-core\python\hub.py
```

Use the URL shown on **Devices**. The QR code contains that same URL.

## Connect Google Calendar

Pace uses your own Google OAuth application.

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **Google Calendar API**.
3. Configure the OAuth consent screen. While testing, add the signing-in account as a test user.
4. Create a **Web application** OAuth client.
5. Add this local redirect URI:

   ```text
   http://127.0.0.1:7462/api/connect/google/callback
   ```

6. Start Pace with your client credentials:

   ```powershell
   $env:PACE_GOOGLE_CLIENT_ID = "your-client-id"
   $env:PACE_GOOGLE_CLIENT_SECRET = "your-client-secret"
   .\.venv\Scripts\python.exe pace-core\python\hub.py
   ```

7. In Pace, open **Connections → Google Calendar → Connect**.

## Choose an AI model

### Arduino Uno Q

Deploy `pace-core` with Arduino App Lab. The Uno Q runs the local Gemma planning bridge; your Hub keeps calendar access and the Pace workspace.

Open `<uno-q-url>/api/state`, copy the device token, then enter the URL and token under **Settings → Arduino Uno Q local model**.

The LED matrix is a status signal: steady marker means ready, moving line means Pace is planning, and a pulse means Pace needs attention.

### OpenAI-compatible local or hosted model

Choose **OpenAI-compatible** in Settings and enter the base URL and model name.

| Runtime | Example base URL |
| --- | --- |
| Ollama | `http://localhost:11434/v1` |
| llama.cpp | `http://localhost:8080/v1` |
| LM Studio | Its local `/v1` server URL |
| Hosted provider | The provider's compatible `/v1` URL |

## Deploy Pace publicly on Render

The repository includes a [Dockerfile](Dockerfile) and [render.yaml](render.yaml). Render can run the Docker service with a persistent disk. Pace stores its SQLite state at `/app/data/pace.db`; mount the configured disk so connections and settings survive redeploys. [Render persistent disks](https://render.com/docs/disks)

1. Push this repository to GitHub.
2. In Render, select **New → Blueprint** and choose the repository.
3. Use a service plan that supports the included persistent disk.
4. After Render assigns a domain, set:

   ```text
   PACE_SHARE_URL=https://your-domain.onrender.com/app
   PACE_OAUTH_CALLBACK_BASE=https://your-domain.onrender.com
   PACE_GOOGLE_CLIENT_ID=your-client-id
   PACE_GOOGLE_CLIENT_SECRET=your-client-secret
   ```

5. In Google Cloud, add:

   ```text
   https://your-domain.onrender.com/api/connect/google/callback
   ```

6. Render generates `PACE_ACCESS_TOKEN`. Copy its value from the service environment and open:

   ```text
   https://your-domain.onrender.com/app?access=PACE_ACCESS_TOKEN
   ```

   Pace saves an access cookie and removes the token from the address bar. Share the original link only with devices you want to pair.

The Docker image binds to `PACE_PORT=10000` on `0.0.0.0`, as required by a Render web service. [Render web services](https://render.com/docs/web-services)

## Current scope

- Google Calendar connection, import, proposal, and approved focus-block creation work.
- Uno Q works as a local planning model and status display.
- Linear, GitHub, and custom MCP cards are product entry points. Add their OAuth credentials before connecting them.
- Pace is a personal workspace. It does not currently have multi-user accounts.

## License

License to be chosen before public release.
