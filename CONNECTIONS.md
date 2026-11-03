# Connecting the channels (email, WhatsApp, Teams, Slack)

Everything below goes in **`backend/.env`**. After editing it, restart:

```
docker compose up --build
```

Then open **http://localhost:8000/channels** — a connected channel shows
`"configured": true`. In the UI's **Compose & Send** panel, tick the channels you
connected, pick a recipient, and send.

Order shown easiest → hardest. You do NOT need all four — connect the ones you want.

---

## 1. Email

### Option A — Mailpit (built-in test inbox, zero signup; best for a demo)
```
EMAIL_ENABLED=true
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_USE_TLS=false
SMTP_USERNAME=
SMTP_PASSWORD=
```
Sent mail appears at http://localhost:8025

### Option B — Real Gmail
```
EMAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your_16_char_app_password     # Google Account > Security > App passwords
SMTP_FROM=you@gmail.com
```

---

## 2. Slack  (easiest real channel — just one URL, no opt-in)
1. https://api.slack.com/apps  ->  Create New App  ->  From scratch  ->  pick workspace
2. Open **Incoming Webhooks**  ->  turn **On**
3. **Add New Webhook to Workspace**  ->  choose a channel  ->  Allow
4. Copy the URL (https://hooks.slack.com/services/T.../B.../xxxx)
```
SLACK_ENABLED=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXXX/XXXX/XXXX
```

---

## 3. Microsoft Teams  (also just one URL, no opt-in)
1. In Teams: a channel  ->  •••  ->  Connectors (or Workflows on newer Teams)  ->  Incoming Webhook
2. Name it  ->  Create  ->  copy the URL
```
TEAMS_ENABLED=true
TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/....
```
Note: Microsoft is moving this from "Connectors" to "Workflows (Power Automate)".
If you don't see Incoming Webhook, create a Workflow "When a Teams webhook request
is received" and paste that URL instead.

---

## 4. WhatsApp  (most setup — Twilio + a one-time recipient opt-in)
1. Sign up at https://twilio.com (free trial)
2. Console  ->  Messaging  ->  Try it out  ->  Send a WhatsApp message (the Sandbox).
   Note the sandbox number (+1 415 523 8886) and the join code.
3. From the RECIPIENT's phone, send `join <your-code>` on WhatsApp to the sandbox
   number. REQUIRED — without this, messages silently never arrive.
4. Copy Account SID + Auth Token from the Console dashboard.
```
WHATSAPP_ENABLED=true
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
```
5. The contact you send to needs a phone in E.164 (+91...), set in the Contacts tab.

---

## Verify
- http://localhost:8000/channels  ->  each connected channel is `configured: true`
- http://localhost:8000/docs       ->  try POST /send or POST /compose
- UI Compose & Send panel          ->  tick channels, pick recipient, send

## Security
Keep real keys ONLY in backend/.env (never in .env.example, never commit .env).
If a key was ever shared, rotate it (regenerate) in that provider's console.
