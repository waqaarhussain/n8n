# n8n on Ubuntu 24.04

A self-hosted n8n deployment with PostgreSQL, an `update-live` command and a private school-email integration for iCloud Calendar and InOut WhatsApp.

## What is included

- A Docker Compose deployment, London timezone and persistent encrypted n8n credentials.
- An optional Caddy HTTPS proxy for your own DNS hostname. Without a hostname, the editor binds to localhost for an SSH tunnel.
- A server-side iCloud wizard: app-specific password, calendar discovery, explicit selection and a read-only connection check.
- An importable workflow that checks a selected Outlook folder every minute. No forwarding required.
- AI extraction of multiple event titles, dates, start/end times, locations and useful notes.
- RSVP deadlines go into notes; missing end times use a clearly labelled 60-minute placeholder. Ambiguous dates, cancellations and changes require review.
- SQLite tracking of processed email content and event UIDs to reduce duplicate events and messages.
- WhatsApp messages headed `Email From School`, followed by a clean subject and the email body. Standard forwarding headers are removed.
- Multiple independently configured InOut recipient keys. Each recipient must connect to InOut themselves.
- Long messages split into numbered parts without locally truncating the body. The conservative default is 350 characters per part because the previous IFTTT/InOut path truncated messages. Provider limits and handset delivery still require a live test. More parts consume more InOut messages.
- Backups before updates; secrets, database and edited n8n workflows are kept outside Git.

This is deployment code and a prepared workflow, not an already-connected Apple/Microsoft account. You supply credentials on your server. No credentials belong in GitHub or chat.

## Install

Run on **Ubuntu 24.04** over SSH:

```bash
curl -fsSL https://raw.githubusercontent.com/waqaarhussain/n8n/main/bootstrap.sh -o /tmp/n8n-bootstrap.sh
sudo bash /tmp/n8n-bootstrap.sh
```

The installer clones to `/opt/waqaar-n8n`, installs Ubuntu's Docker packages if Docker is absent, and asks for:

1. **A hostname**, such as `n8n.example.com`. Its DNS must point to this server and ports 80/443 must be available for the bundled HTTPS proxy. Leave blank for localhost access using an SSH tunnel. It does not modify your firewall.
2. **Apple Account email and an app-specific password**. Generate one at https://account.apple.com/ under Sign-In and Security → App-Specific Passwords. Apple two-factor authentication must be enabled. Do not use your normal Apple password.
3. **An iCloud calendar**, selected from the discovered list. Create a separate `School` calendar in Apple Calendar first if wanted. No new calendar is silently created and no default-calendar fallback is used.
4. **An OpenAI API key**, used for event extraction. ChatGPT Plus does not include API billing. Blank leaves extraction unconfigured. The default model is `gpt-4.1-mini` and can be changed during setup.
5. **InOut API keys**, one per recipient. Each person sends `Create APIKey` to the InOut WhatsApp bot in their own chat. InOut's allowance/subscription is separate. HTTP acceptance does not prove delivery. Multiple-recipient delivery and provider-added message introductions are not verified until you test them.

The setup wizard lists calendars but does not write a test event or send WhatsApp messages.

For local access, run this **on your computer**, keeping SSH open:

```bash
ssh -L 5678:127.0.0.1:5678 YOUR_SSH_USER@YOUR_SERVER
```

Then open http://localhost:5678. With a configured hostname, use `https://YOUR_HOSTNAME`.

Create your n8n owner account in the browser. This is n8n's normal account setup; no shared/default editor password is installed.

If another service already uses ports 80/443, do not start a second proxy. Use localhost mode initially, then configure your existing HTTPS proxy and update `.env` (`N8N_HOST`, `N8N_PROTOCOL=https`, `N8N_PUBLIC_URL`, `N8N_PROXY_HOPS=1`). Keep `N8N_BIND=127.0.0.1`. See `docs/reverse-proxy.md`.

## Watch the Shrubbery folder in Outlook

**Direct folder watching is the default.** It checks once a minute, rather than instant push. Server-side Outlook rules should deliver new school mail to the Shrubbery folder. Messages manually moved there much later may be missed because the native trigger uses received timestamps. Existing messages are not backfilled automatically.

### Connect a personal Outlook/Hotmail account

Self-hosted n8n requires your own Microsoft OAuth application. The hosted n8n Cloud one-click Microsoft connection is not bundled with Community Edition.

1. Open your n8n instance and create a **Microsoft Outlook OAuth2 API** credential. Copy its displayed OAuth callback URL.
2. Register an application in Microsoft's application registration portal: https://aka.ms/appregistrations.
3. Select an account type that includes **personal Microsoft accounts**. Add a **Web** redirect URI equal to the callback URL from n8n.
4. Create a client secret. Enter its **Value**, and the Application (client) ID, into the n8n credential. These are saved in n8n's encrypted credential store, not in this repository.
5. Select **Connect my account** and sign in to your **personal Outlook/Hotmail account**. This workflow is for personal school mail, not Stonewater work mail.
6. Follow the fields and consent requested by the Outlook credential. Official guide: https://docs.n8n.io/integrations/builtin/credentials/microsoft/.

Use a hostname with HTTPS for the simplest remote OAuth experience. If you cannot register an app, a forwarding rule to a separate IMAP mailbox is an alternative, but this repository does not provision a mailbox or email address.

### Import and configure the workflow

After creating your n8n owner account:

```bash
sudo n8n-live import
```

Alternatively import `workflows/school-outlook.json` through the n8n editor.

The imported workflow is **inactive**. In **Watch Shrubbery folder**:

- Select your Outlook OAuth credential.
- **Filters → Folders to Include:** replace `SELECT_SHRUBBERY_FOLDER` with your actual **Shrubbery** folder using the folder picker. The placeholder deliberately prevents watching the whole mailbox by accident.
- Keep **Output = Raw**, so the full body is available. Simplified output only contains a preview.
- Keep **Read Status = Unread and read messages**. Reading an email on your phone must not exclude it.
- Keep the polling interval at **Every Minute**.
- Attachments are disabled. This version reads the email body, not PDFs, images or attached invitations.

The HTTP Request node calls the private `school-bridge` Docker service. It requires no community nodes, public webhook or extra n8n credential. The bridge is not exposed on a host port.

Run a single controlled test, check the output and iCloud event, then **publish/activate** the workflow. A manual trigger test may select an older message; choose a disposable test email first. Once satisfied, turn off the old IFTTT applet to avoid two platforms processing the same email.

A `review` result or uncertain WhatsApp delivery is routed to a failed execution with a reason. Inspect **Executions**; an error workflow or additional notification can be added later. A review result is cached: after manually resolving the calendar entry, do not expect resending the same email to reinterpret it automatically.

## Updates and commands

```bash
sudo update-live                 # where no other update-live existed
sudo n8n-live update              # always works for this project
sudo n8n-setup                    # reconfigure iCloud, AI and WhatsApp
sudo n8n-live check-icloud        # read-only connection check
sudo n8n-live status
sudo n8n-live logs
sudo n8n-live backup
sudo n8n-live upgrade-engine      # explicitly pull/pin the current n8n stable image
```

An existing unrelated `/usr/local/bin/update-live` is preserved; it is not overwritten. Updates use a fast-forward Git pull and rebuild the bridge. They do not reimport workflow templates over your edits. Re-running `import` updates the template workflow, so do not do this casually after editing it.

The n8n engine is pinned to the image digest resolved during installation. Normal `update-live` does not jump engine versions. `upgrade-engine` makes a backup and explicitly selects the then-current stable digest. PostgreSQL stays on major version 16.

Local files:

- `.env`: generated database password, n8n encryption key and deployment settings. Root-readable only.
- `config/settings.json`: Apple app-specific password, selected calendar, AI key and recipient keys. Root-owned, readable by the bridge container's group only.
- `data/state.sqlite3`: processed-content fingerprints, extracted event data and per-recipient delivery states. Back up this file to preserve duplicate protection.
- `backups/`: private snapshots including secrets, n8n data, database dump and Git revision. Copy backups off-server securely. Automatic retention/deletion is not enabled.

## Reliability and limits

- The AI model runs remotely; this server coordinates API calls. The email body is sent to the configured OpenAI API and to each configured InOut recipient. It is not uploaded to this repository.
- Matching duplicate email content uses the cleaned subject/body. Changed content can legitimately be processed again. Event deduplication uses a normalised title plus start date/time. This is not a universal semantic duplicate detector and does not deduplicate existing IFTTT events.
- Deterministic CalDAV UIDs and resource URLs make retries reuse the same event resource after an interrupted write.
- A WhatsApp timeout is ambiguous: InOut may already have accepted it. The bridge records it as uncertain and does **not** automatically resend that part. Inspect the recipient chat/provider first. No automatic delivery receipts or retry administration UI is included.
- Microsoft polling cursors can advance before downstream failures. A failed execution needs a deliberate retry from n8n; retries are protected by the content/event state. If email arrival triggers are mission-critical, add a scheduled reconciliation workflow before relying on this unattended.
- iCloud connection checks verify credentials and calendar discovery, not write permission. A shared read-only calendar will fail on the first write. Select an owned/writable calendar.
- The system does not process attachments, automatically update cancellations/reschedules, send to arbitrary WhatsApp numbers through one key, or remove provider-imposed message templates.
- AI output is validated for schema, date ordering, London offsets and an evidence excerpt present in the source. It can still misunderstand an email. Review the first live events.

## Restore

Stop `n8n` and `school-bridge` before restoring. Use the **matching** `.env`/encryption key, `config`, `data`, n8n volume archive and PostgreSQL dump from one backup. Do not merge a new encryption key with old credentials. See `docs/restore.md` for commands. Restore can overwrite current data; take a fresh backup first.

## Checks

```bash
python3 -m venv .venv
.venv/bin/pip install -r bridge/requirements.txt pytest
.venv/bin/python -m pytest -q
bash -n bootstrap.sh install.sh scripts/n8n-live
```

Tests cover forwarding-header cleanup, complete Outlook bodies, event timing, London offsets, duplicate emails/events, multi-recipient fan-out, uncertain-send handling, calendar selection and workflow defaults. CI also validates Compose and builds the bridge image.

The prepared code has local automated tests. Ubuntu deployment, Microsoft OAuth, iCloud writes and InOut handset delivery must be verified on the actual server/accounts.
