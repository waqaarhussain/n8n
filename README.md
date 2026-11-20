# n8n on Ubuntu 24.04

A self-hosted n8n deployment with PostgreSQL, an `update-live` command and a private school-email integration for iCloud Calendar.

## What is included

- A Docker Compose deployment, London timezone and persistent encrypted n8n credentials.
- The standard n8n web UI at `https://YOUR_PUBLIC_IPV4` or your DNS hostname, through Caddy. Public-IP mode explicitly requests short-lived publicly trusted Let's Encrypt certificates and renews them automatically. SSH tunnel mode remains available.
- A server-side iCloud wizard: app-specific password, calendar discovery, explicit selection and read-only calendar/mail connection checks.
- An importable workflow that listens for incoming iCloud Mail forwarded to a dedicated alias. No Microsoft/Entra app registration is required.
- AI extraction of multiple event titles, dates, start/end times and useful notes.
- Dated parent actions (forms, RSVP, bookings and payments) get their own deadline entries; missing activity end times use a 60-minute default. Ambiguous dates, cancellations and changes require review.
- SQLite tracking of processed email content and event UIDs to reduce duplicate events.
- Backups before updates; secrets, database and edited n8n workflows are kept outside Git.

This is deployment code and a prepared workflow, not an already-connected Apple/Microsoft account. You supply credentials on your server. No credentials belong in GitHub or chat.

## Install

Run on **Ubuntu 24.04** over SSH:

```bash
curl -fsSL https://raw.githubusercontent.com/waqaarhussain/n8n/main/bootstrap.sh -o /tmp/n8n-bootstrap.sh
sudo bash /tmp/n8n-bootstrap.sh
```

The installer clones to `/opt/waqaar-n8n`, installs Ubuntu's Docker packages if Docker is absent, and asks for:

1. **Your server public IPv4 address**, or a hostname such as `n8n.example.com`. The web UI opens at `https://YOUR_IP` with no port suffix. Ports 80/443 must be reachable from the internet and free for the bundled HTTPS proxy. Public-IP certificate issuance uses HTTP validation on port 80. For a hostname, DNS must point to this server. Leave blank for an SSH tunnel instead. The installer does not modify your firewall.
2. **Apple Account email and an app-specific password**. Generate one at https://account.apple.com/ under Sign-In and Security → App-Specific Passwords. The Apple Account defaults to `waqaar06@hotmail.com`. Do not use your normal Apple password.
3. **An OpenAI API key**, used for event extraction. ChatGPT Plus does not include API billing. The shared model is fixed to `gpt-6.1-sol`.

The primary mail login is fixed to `hackdroid@icloud.com` and the forwarding alias to `waqaarn8n@icloud.com`. Setup verifies read-only IMAP access and can reuse the Apple app-specific password. These addresses and the model are public project defaults; passwords and API keys stay only on your server.

General setup does not ask for a calendar. Run `sudo school-import` to discover calendars and choose one by number before importing the email workflow. No calendar is created automatically. The bridge currently has one shared calendar destination, so importing again changes that destination for all workflows using this bridge.

API key entry shows a live masked first/last-character preview in an interactive terminal and confirms it after Enter. Short keys are masked more heavily; Apple passwords remain hidden. Without a terminal, the prompt falls back to hidden entry followed by masked confirmation.

For local access, run this **on your computer**, keeping SSH open:

```bash
ssh -L 5678:127.0.0.1:5678 YOUR_SSH_USER@YOUR_SERVER
```

Then open http://localhost:5678. With a configured public IP or hostname, use the `https://...` URL printed by the installer. Certificate issuance may take a short time; inspect `docker compose logs caddy` if HTTPS is unavailable. Do not bypass certificate warnings.

Create your n8n owner account in the browser. This is n8n's normal account setup; no shared/default editor password is installed.

If another service already uses ports 80/443, do not start a second proxy. Use localhost mode initially, then configure your existing HTTPS proxy and update `.env` (`N8N_HOST`, `N8N_PROTOCOL=https`, `N8N_PUBLIC_URL`, `N8N_PROXY_HOPS=1`). Keep `N8N_BIND=127.0.0.1`. See `docs/reverse-proxy.md`.

## Forward school email to your iCloud alias

The default now uses **iCloud Mail**, avoiding Microsoft Entra/Azure app registration entirely.

An iCloud email alias is another address for the same mailbox, not a separate account. Leave iCloud Mail disabled in your phone Mail app if you do not want it displayed there. The Ubuntu server connects directly to Apple's IMAP service. Keep the alias active in iCloud and confirm it receives a test email through iCloud.com Mail.

### Outlook forwarding rule

In Outlook.com on the web, open **Settings → Mail → Rules** and create a rule matching your existing school-message criteria. Add a **Forward to** action addressed to your automation alias. Keep the existing action that moves the mail to **Shrubbery**, if wanted.

The rule must act on new arrivals, not depend on somebody manually moving a message later. A server-side rule runs even when Outlook on the phone is closed. Test one new message to ensure it reaches iCloud. Do not set the entire Hotmail mailbox to forward.

### Import and configure

Setup uses the saved primary iCloud login and forwarding alias above. Supply an Apple app-specific password. The wizard verifies `imap.mail.me.com`, port `993`, TLS, and read-only access to `INBOX`. It does not enable Mail on your phone.

After creating your n8n owner account in the browser:

```bash
sudo school-import
```

This imports your configured IMAP credential into n8n's encrypted credential store and substitutes the saved alias into the workflow. Temporary credential import files are removed afterwards. The workflow is **inactive** until you review and publish it. The fixed email-address defaults are public; credentials and API keys are never committed.

Open **School email via iCloud alias → iCloud Calendar** and confirm:

- Credential: **School iCloud IMAP**.
- Mailbox: **INBOX**.
- Format: **Resolved**, with attachment prefix `attachment_`. This fetches the full message source and parses its body/attachments locally instead of issuing separate body/attachment downloads. The bridge accepts both the older Simple fields and Resolved fields.
- Action: **Nothing**, so the trigger does not mark messages read.
- Custom Email Rules: `['ALL', ['TO', 'your-automation-alias@icloud.com']]` using valid JSON with double quotes. The import command fills this in automatically.
- Fetch Only New Emails is enabled.

Both the trigger's IMAP filter and the bridge check the intended recipient address. Unrelated mail is ignored before AI extraction, calendar writes. This is recipient scoping, not a sender-authentication mechanism: anyone knowing the alias can send to it, so keep the address private. Forward normally with the alias in To, not Bcc, so both filters can match it.

The IMAP trigger listens for mailbox arrivals. Delivery depends on Outlook forwarding and iCloud, so do not treat it as guaranteed instant delivery. Initial activation can include matching emails from earlier that day. Test with a disposable email first. PDF, DOCX and DOC attachments are read when using the updated iCloud workflow.

The HTTP Request node calls the private `school-bridge` Docker service. It requires no community nodes or public webhook. The bridge is not exposed on a host port.

A `review` result is routed to a failed execution with a reason. Inspect **Executions**. A review result is cached: resending the same content does not reinterpret it automatically.

If the earlier direct-Outlook workflow was imported, deactivate it before publishing the iCloud one. Turn off the old IFTTT applet after the new route passes its live test to avoid duplicate processing across systems. The optional `workflows/school-outlook.json` template remains available for people who have configured Microsoft OAuth, but it is no longer the default.

## Updates and commands

```bash
sudo update-live                 # where no other update-live existed
sudo n8n-live update              # always works for this project
sudo n8n-setup                    # reconfigure iCloud and AI
sudo n8n-live check-icloud        # read-only connection check
sudo n8n-live web                 # change web UI IP/hostname without replacing credentials
sudo n8n-live status
sudo n8n-live logs
sudo n8n-live backup
sudo n8n-live upgrade-engine      # explicitly pull/pin the current n8n stable image
```

An existing unrelated `/usr/local/bin/update-live` is preserved; it is not overwritten. Updates use a fast-forward Git pull and rebuild the bridge. They do not reimport workflow templates over your edits. Re-running `import` updates the template workflow, so do not do this casually after editing it.

The n8n engine is pinned to the image digest resolved during installation. Normal `update-live` does not jump engine versions. `upgrade-engine` makes a backup and explicitly selects the then-current stable digest. PostgreSQL stays on major version 16.

Local files:

- `.env`: generated database password, n8n encryption key and deployment settings. Root-readable only.
- `config/settings.json`: Apple app-specific password, selected calendar, AI key. Root-owned, readable by the bridge container's group only.
- `data/state.sqlite3`: processed-content fingerprints, extracted event data. Back up this file to preserve duplicate protection.
- `backups/`: private snapshots including secrets, n8n data, database dump and Git revision. Copy backups off-server securely. Automatic retention/deletion is not enabled.

## Reliability and limits

- The AI model runs remotely; this server coordinates API calls. The email body is sent to the configured OpenAI API. It is not uploaded to this repository.
- Matching duplicate email content uses the cleaned subject/body. Changed content can legitimately be processed again. Event matching combines occurrence date/time, event identity, audience and practical details. Exact matches reuse a stable event UID; differing titles with plausible matching occurrences receive a conservative OpenAI comparison. It does not retroactively deduplicate unrelated pre-existing/IFTTT entries.
- Deterministic CalDAV UIDs and resource URLs make retries reuse the same event resource after an interrupted write.
- Mail trigger cursors can advance before downstream failures. A failed execution needs a deliberate retry from n8n; retries are protected by the content/event state. If email arrival triggers are mission-critical, add a scheduled reconciliation workflow before relying on this unattended.
- iCloud connection checks verify credentials and calendar discovery, not write permission. A shared read-only calendar will fail on the first write. Select an owned/writable calendar.
- The system does not automatically update cancellations/reschedules.
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

Tests cover forwarding-header cleanup, complete Outlook bodies, event timing, London offsets, duplicate emails/events, calendar selection, alias scoping and workflow defaults. CI also validates Compose and builds the bridge image.

The prepared code has local automated tests. Ubuntu deployment, iCloud Mail login, iCloud writes must be verified on the actual server/accounts.



## Existing installations: calendar-only update

```bash
sudo n8n-live update
sudo n8n-live remove-whatsapp
```

The second command removes saved InOut recipient keys from the current settings file without changing Apple credentials, the selected calendar, the OpenAI key/model or the mail alias. Normal subsequent updates perform this cleanup too. Older private backups retain their original settings. InOut sending is absent from the application even if older settings are restored. TextMeBot is available separately as an opt-in integration.

Do not reimport the workflow to apply this update: the already-published workflow calls the updated bridge and remains compatible. Its old display name may still mention WhatsApp; rename it in the editor if wanted. No email notifications are sent unless optional TextMeBot is explicitly configured and enabled. Emails with no events finish without creating calendar entries; ambiguous event details require review. Existing email and event duplicate protection is preserved.

## Optional TextMeBot trial

InOut is removed. TextMeBot is a separate opt-in integration. Complete the add-number and WhatsApp-linking steps in the email from TextMeBot first. Its documented endpoint is `https://api.textmebot.com/send.php` with `recipient`, `apikey` and `text` parameters.

```bash
sudo n8n-live update
sudo n8n-live setup-textmebot
sudo n8n-live check-textmebot
# Only after seeing the test message on your phone:
sudo n8n-live enable-textmebot
```

Setup prompts only for the TextMeBot key (masked confirmation) and recipient phone number with country code, preserving Apple and OpenAI settings. Automatic messages stay disabled until explicitly enabled. The check command sends one short test to the saved recipient. Provider HTTP responses are not delivery receipts. No live handset delivery or provider message limits are assumed from unit tests.

Once enabled, messages contain `Email From School`, the subject and the body with recognised school signature/disclaimer blocks removed. Newlines and full URLs are preserved in requests. Long text is split at up to 3,000 characters per part; actual provider acceptance and rendering require testing. Calendar writes finish before notification sending; notification errors are reported separately and cannot undo saved events. Ambiguous sends are held instead of retried automatically. Existing imported workflows remain compatible.

`sudo n8n-live remove-whatsapp` now removes both old InOut and optional TextMeBot settings. Normal updates remove only obsolete InOut keys and preserve TextMeBot configuration. Your private older backups can still contain the original saved keys.

## Document attachments and cleaner calendar notes

The iCloud workflow now downloads attachments and uses a Code node to transfer actual binary buffers to the private bridge. Supported documents: PDF, DOCX and legacy DOC, plus JPG/JPEG and PNG image flyers. PDF/Word text extraction is local; scanned PDF pages use English Tesseract OCR. With OpenAI configured, JPG/PNG flyers are sent as actual images to that model for vision transcription, then the transcript is used for event extraction. Extracted document text is sent to the configured OpenAI API along with the email subject/body. JPG/PNG attachments are vision-read with OpenAI when configured, including decorative image flyers; local OCR remains a fallback when no OpenAI key is configured. Decorative logos should be ignored by the event extractor. Animated GIF signatures are skipped. Image-only Word documents need manual review. Attachments are processed in temporary directories and removed after processing; n8n execution storage can still retain input data.

Limits: 10 documents, 10 MiB each, 20 MiB combined, 30 pages per PDF, 90 seconds combined document processing with at most 45 seconds per document, and 120,000 extracted document characters. Protected, corrupt, unsupported or unreadable documents require review and prevent automatic calendar writes for that email. OCR can misread text; uncertain dates remain subject to event validation/review.

For an existing installation, update the server and reimport the workflow to obtain attachment downloading and the preparation node. This deliberately replaces the task-owned template workflow; record any personal node edits first. Stop/unpublish the old workflow in the editor before importing, then review and publish the imported version again. Do not manually execute old cached test data to test attachments.

```bash
sudo n8n-live update
sudo school-import
```

Calendar descriptions contain only the practical event summary. Source, Evidence, assumptions and missing-detail commentary are not appended. Evidence and structured assumptions stay internal for validation; the default duration remains 60 minutes. Reforwarding an existing message can refresh that event's notes at the same stable CalDAV URL, without creating a second event. Outputs distinguish `events_created` and `events_updated`.

## Overview statistics while testing

```bash
sudo n8n-live stats-reset  # numbered workflow picker; 0 resets all workflow overview counters
sudo n8n-live stats        # numbered workflow picker; toggles that workflow's stats pause/resume
```

The reset backs up Insights tables and briefly stops/restarts n8n so buffered counts are flushed before clearing. It deletes only the selected overview-counter data, not execution logs, workflows, credentials, calendar events or bridge duplicate tracking. Refresh the Overview page afterwards.

The pause helper keeps workflows running and execution history available. It installs a local PostgreSQL trigger on `insights_raw` that zeroes metric values for the selected workflow while paused. Time windows also exclude buffered records flushed after resuming. Counts from before pausing remain. A run is classified by its completion/Insights timestamp. This is a local extension to n8n, not a native n8n feature; schema guards reject incompatible versions. It has been checked against n8n 2.47.5 source and tested with raw-to-counter data in PostgreSQL. Recheck it before upgrading n8n across database schema changes.

## Helper menu

Run `helper` for a numbered menu with a description of every n8n maintenance command. Selecting a command continues its existing flow, including the workflow picker for stats. The selected helper reacquires the maintenance lock normally; menu dispatch releases its own lock first. This menu covers this n8n project, not unrelated applications on your server.

For an installation predating the menu, run `sudo n8n-live update`, then `sudo n8n-live helper` once. That first menu invocation also installs the `helper` shortcut. If a different application already owns `/usr/local/bin/helper`, it is preserved; use `sudo n8n-live helper` for this menu.

The extractor compares the subject, email prose and every readable attachment to find the actual event name and useful practical notes. Generic subjects such as Flyer and forwarding prose such as FYI/see attached do not become event titles or descriptions. Uniform requirements, costs, RSVP details, what to bring and collection instructions can be included when supported by a source. Old cached extractions refresh once after this extraction-rule update, while stable event identity and duplicate protection remain. Image OCR and final model interpretation still require a live test with your actual flyer.

For a clear school activity date without an exact clock start time (including an afternoon visit or PE-kit reminder), extraction creates an all-day date marker and preserves the stated period in the description. It does not invent a start time. Exact start times without end times still use the configured duration. Descriptions retain substantive email paragraphs relevant to that event, removing greetings/headers/signatures/disclaimers and generic attachment introductions; useful attachment-only details are added. A generic FYI body instead uses attachment content. This preserves useful body wording rather than requiring a short summary.

## Reviewing a blocked event

`sudo n8n-live review-last` (also in `helper`) lists recent cached event titles, proposed start dates and supporting source quotes. It does not call OpenAI, send notifications, write calendar events or read API keys. The processing node also returns `review_details` for a supporting-quote mismatch.

Evidence matching normalises display typography (curly quotes, Unicode dashes, whitespace and invisible formatting), but does not change dates, numbers, ordinal suffixes or time punctuation. The extractor is instructed to copy one contiguous source excerpt rather than merging/paraphrasing different parts of the email. Unsupported proposals remain blocked for manual review.

## Decorative image flyers

Conventional local OCR can miss large decorative date/time banners. JPG/PNG images now use the configured OpenAI model's image input, producing a text transcription before event extraction. The separate transcription request uses the existing OpenAI API key/model and is billed as additional API usage. Successful image transcripts are cached by file content and model. No new key is required. Image-support failures are surfaced for review rather than silently accepting incomplete local OCR.

Processing output includes `attachment_summary`: filenames, MIME types and the reader used, without binary data or API keys. An empty list means no attachment bytes reached the bridge. In that case inspect the preparation node and ensure the image was forwarded as an actual attachment; browser-visible externally hosted email images are not fetched automatically. The Review email node terminates that execution, not waits for a browser acknowledgement. Other published executions do not require dismissing its error toast.

## Mail listener catch-up

The template keeps normal IMAP arrival handling but forces reconnect/catch-up every minute. This is a backstop for a mailbox connection whose arrival notifications stop, not a guarantee about Outlook/iCloud delivery speed. The n8n 2.47.5 IMAP connection implementation invokes `catchUp()` after reopening the mailbox.

For an existing published workflow, avoid importing over your edits: open Watch iCloud school alias, set Options → Force Reconnect Every Minutes to 1, save and publish that change. There is no need to reset email/event history. This setting must be applied in the UI; a server code update alone does not overwrite your published node parameters.

`sudo n8n-live check-mail` (also in `helper`) logs into the saved primary iCloud mailbox, opens INBOX read-only and lists the latest five messages matching the automation alias with their internal mailbox-arrival timestamps, subjects and To addresses. It fetches headers with BODY.PEEK, does not change seen flags, does not read message bodies and does not print passwords. Use it to separate provider delivery delays from a trigger/listener issue. The UTC/London date changing at midnight does not itself stop an already-published IMAP trigger.

## Moving to a fresh VPS

The bootstrap command installs a fresh instance. It does not transfer old n8n users, workflow edits, duplicate history, stats or encrypted credentials. Restore a private backup if those must carry over. For a fresh install, configure credentials, create the n8n owner account, run `sudo school-import`, choose the calendar, then publish. Disable the old VPS workflow before publishing the new one so both listeners do not process the same emails. Keep the old VPS available until the new one passes a real email test.

## Telegram failure alerts for selected workflows

Telegram alerts are optional and work across this n8n installation, not only the school integration. **Every workflow starts OFF**, including school and workflows created/imported later. `school-import` never subscribes it automatically.

The fresh VPS installer offers Telegram setup. Paste your bot token at the hidden prompt (a masked first/last-character confirmation follows). Open the one-time private bot link printed by the wizard and press Start, then return to the terminal. The wizard matches that exact private Start message and sends one connection test; it never guesses the recipient from another chat. Use a dedicated alert bot without an existing webhook. If setup is skipped, the helper can configure it later.

After importing/creating a workflow, run:

```bash
sudo telegram
```

The numbered list shows every workflow and its current ON/OFF state. Pick a number to toggle failure alerts for that workflow. Pick `s` to replace a revoked token/reconnect the private chat, `t` for a connection test, or `0` to exit. The same flow is in `helper`. Updating the token preserves the selected workflows; connecting Telegram alone does not add subscriptions. `update-live` remains the updater.

Alerts show the workflow name, Failed/Crashed status, London time, execution ID and an **Open failed execution** button. Raw error details, email bodies, attachments, input data, tokens and stack traces are not sent. Open the failed execution to see the actual reason. No workflow nodes or settings are rewritten and no republish is needed to toggle alerts.

A separate `n8n-telegram.service` checks saved n8n executions every 20 seconds. Only executions created after a workflow's latest enable time qualify; older failures are not replayed. Error and crashed statuses qualify; canceled, successful and in-progress executions do not. Manual failed executions qualify if n8n saves them. Keep failure execution saving enabled for monitored workflows. A workflow that never triggers creates no failed execution, so this is not an incoming-email or whole-VPS outage alarm.

Delivery state persists across restarts and successful sends are deduplicated. Telegram/network failures back off and retry while the failed execution is retained in n8n. An ambiguous network timeout or a crash after Telegram accepts a send can still produce a duplicate on retry. Very busy failure queues take longer; the poll interval is not a delivery guarantee. If this VPS/database is down, alerts cannot be sent until it recovers. Old records pruned before delivery cannot be recovered by the monitor.

The monitor reads n8n tables in read-only transactions and validates the required schema. Its root-only token/chat settings are in `config/telegram.json`; opt-in and delivery state are in `data/telegram.sqlite3`. Both are included in private maintenance backups. The bot token is never embedded in the repository, workflow exports or curl command. Backup pauses the monitor while copying SQLite state. Setup/update install the systemd service without enabling any additional workflows.

Monitor status/logs:

```bash
sudo systemctl status n8n-telegram.service
sudo journalctl -u n8n-telegram.service -n 30 --no-pager
```

## Existing school workflow: full-message IMAP download

New imports use **Resolved** format. The native n8n IMAP implementation fetches the complete MIME message in this mode and parses it locally; Simple downloads HTML, plain text and attachments separately. This removes those separate download steps but does not guarantee faster delivery or instantaneous pickup. The trigger completes its email/attachment download before n8n records an execution start.

For an existing published workflow, change only **Watch iCloud school alias → Format → Resolved**, keep the attachment prefix `attachment_`, then save and publish. Leave **Force Reconnect Every Minutes = 1**, recipient rules and other nodes unchanged. Normal `update-live` updates the template/backend without overwriting the published workflow's node settings. No reimport is needed for this one-field editor change. Compare a fresh forwarded email's mailbox arrival time with its exact execution start to assess the effect. Switching the format back to Simple is supported if needed.

## Fast email pickup

Fresh installs and `sudo update-live` load a small IMAP enhancement through a read-only mount and Node preload. The published workflow, credentials, nodes, calendar settings and duplicate tracking are preserved. **No workflow reimport or republish is needed.** The official n8n image remains pinned; its installed files are not modified.

The native push listener stays active. A lightweight **NOOP plus UID-only search every 10 seconds** uses the same authenticated connection to catch new messages even when an arrival notification is delayed. Idle checks do not download old bodies or attachments. New UIDs request the native n8n catch-up path, which applies the existing alias filter, attachment handling and UID tracking. A slow fetch is coalesced instead of accumulating extra polling jobs. Native n8n retains responsibility for reconnecting; credential tests do not gain polling timers. Shutdown/unpublish cancels the additional check.

This targets the wait before workflow execution. It does not promise a ten-second end-to-end calendar update: server command delays, connectivity, attachment downloading, OpenAI and CalDAV processing still take time. Existing one-minute forced reconnect settings can remain as a separate recovery backstop.

The enhancement is verified against n8n 2.47.5's actual IMAP library in CI. If an engine upgrade changes the required internals, it logs a short warning and preserves native behaviour instead of taking n8n offline. Check `sudo n8n-live logs` for `Fast mail enabled` after updating. The preload never logs credentials, subjects, recipients or email bodies.

For an explicit opt-out, add `N8N_FAST_MAIL_SECONDS=0` to `.env`, then run `sudo docker compose up -d n8n` from the project directory. The default is 10; supported non-zero intervals are 5–60 seconds. Removing the bind mount/NODE_OPTIONS preload also restores the native listener without changing workflows.

## School audience and stronger duplicate matching

Duplicate protection is always enabled. New extraction metadata identifies the underlying occasion and the intended audience from the subject, substantive email body and readable attachments together. Matching uses occurrence date/time plus identity, audience and event details, never just a clock time or a similar title. The index is scoped to the selected calendar. It also handles duplicate notices within one email/batch.

Repeated notices reuse the original calendar UID/URL. Useful new paragraphs are merged into that entry rather than discarded. Fair/Fayre spelling variants match. A Santa's Grotto explicitly held as part of the Winter Fair, for the same complete event window and with no separate visit appointment, is folded into the single Winter Fair entry. Ticket/booking/adult-accompaniment details stay in its notes. Mufti Day remains a separate event. A separately timed grotto booking, different activity at the same time, conflicting date/time, or separate cohort session must not be silently collapsed. Possible matches with unclear/conflicting evidence require review. Non-exact matching can make an additional OpenAI request; successful title variants are remembered locally.

The default selected cohorts are **Nursery, Reception and Pre-Prep**, with case-insensitive matching and common Pre Prep/preprep/hyphen variants. Events explicitly limited to other groups, including Years 1–6, are excluded. Each event in a newsletter is evaluated independently. **Whole-school/family events and events with no specified year group are included.** A generic mention of older years elsewhere in a newsletter does not exclude relevant younger-child activities.

To change the cohorts later:

```bash
sudo school-groups
```

Choose allowed groups by number, separated by commas. For example, choose Year 1 and Reception when those become the children's groups. Explicit Pre-Prep notices remain relevant while Nursery or Reception is selected. The helper preserves Apple/OpenAI settings and needs no workflow reimport/republish. Policy changes invalidate the relevant extraction cache. Existing calendar events are not deleted when the selected groups change.

### Fresh manual duplicate test

Keep the Outlook forwarding rule off during this test. Update, then clear the old processing history:

```bash
sudo update-live && sudo school-reset
```

The reset briefly stops/restarts n8n and the bridge, creates a private SQLite backup, then clears processed-email extraction, event-match/alias records and cached image transcription. Duplicate protection stays **ON** immediately afterwards. It does not delete iCloud inbox messages, existing calendar entries, execution logs, stats, credentials or Telegram selections. **Remove the old test entries from the School calendar yourself before forwarding the examples again**, otherwise old calendar duplicates remain outside the fresh local index. Do not use the earlier manual `DELETE FROM mail/events` snippet now: use this helper so the additional matching tables are cleared consistently.

Forward the Mufti Day/Winter Fair notice and the grotto notice. The expected outcome is two calendar entries: Mufti Day, and Winter Fair including the grotto details. Re-forwarding either notice should reuse those entries. Once this passes with the actual emails/attachments, enable the Outlook rule. This is a backend update; no school workflow reimport or republish is needed. Both helpers are also listed in `helper` and installed by fresh curl installs.

## School location field removed

Location is no longer requested as a structured OpenAI field, written to the calendar LOCATION property, included in event signatures, or used by duplicate matching. Old cached/indexed records containing a location still load safely. Practical venue wording can remain in the substantive email description.

A reminder changing "In school" to "School hall, The Shrubbery School" can now update the same occurrence. If the earlier email supplied no end time and the calendar used the default hour, a newly stated 30-minute duration replaces that placeholder. Conflicting explicit dates/times still require review; year-group filtering and unrelated-event protection remain active.

Apply with `sudo update-live`. Keep the current history, publish state and workflow. Forward the held reminder again as a new email to reprocess it; do not retry the saved final Review-email node or run school-reset. The matched entry reuses its UID, receives the stated end time and drops the structured location field on its next successful update. Other existing calendar entries are refreshed only when subsequently processed.

## Ordinary conversations and general advice finish successfully

OpenAI classifies the current substantive message before extracting calendar actions. Attendance acknowledgements, reserved-place confirmations, thanks, jokes/banter and ordinary back-and-forth replies with no new calendar action return `status: ignored`, a plain reason and zero events created/updated. General advice such as wearing warm hats/coats in cold weather also finishes successfully when it does not announce a scheduled activity or dated special requirement. These runs take the normal successful workflow path and do not count as failures.

Reply/forward headers and HTML quotation boundaries are supplied as supplemental intent context. An old date/event subject or a missing letter referenced only in quoted history is not enough to turn an ordinary reply into a fresh event notice. A generic FYI forwarding wrapper around a real school notice still gets processed as an event notice. Dated PE-kit reminders, special hat days and newly scheduled activities remain actionable. Actual new event details, cancellations or date/time changes are not discarded as conversation.

Non-actionable decisions must contain no extracted events or actionable evidence. Contradictory classification still requires review. Genuine event conflicts, current missing/unreadable essential attachments, failed evidence validation and provider/network errors retain their existing safeguards. There is no blanket conversion of failures to success and no keyword-only banter filter.

Apply with `sudo update-live`. No workflow reimport, republish or reset is needed. A newly forwarded copy of a previously failed conversational reply is reclassified under the updated extraction version; existing calendar entries are left alone. Old execution records and previously accumulated statistics are historical and are not rewritten. Use the existing per-workflow stats-reset helper separately if you want a fresh Overview baseline.

### Weekly VPS package updates

Run `sudo update-live`, then `sudo n8n-live vps-import` on the n8n host.
The importer privately asks for the VPS IP, a display name and hidden root SSH
password (port 22); credentials are imported into n8n's encrypted credential store,
not written into the workflow export. Re-import refuses to replace an existing
maintenance workflow. Edit the SSH credential or the **VPS display name** node
in n8n for later changes.

The new **VPS weekly updates** workflow is initially inactive. Verify the SSH
credential and publish it to schedule Mondays at 00:00 **Europe/London**, including
UK daylight-saving changes. The manual trigger runs the actual upgrade too.
It uses `apt-get update` and `apt-get upgrade -y` noninteractively, keeps existing
configuration files when a decision is required, waits up to ten minutes for apt
locks and prevents overlapping copies of its own update script. Repository errors,
nonzero exit codes and missing reboot-check output fail the workflow. Services
can restart during upgrades. It never runs reboot, full-upgrade or autoremove.
It checks `/var/run/reboot-required` after a successful upgrade.

Run `sudo telegram` and enable **VPS weekly updates**. The existing bot and
per-workflow opt-in then cover ordinary failure alerts and successful maintenance
runs requiring reboot. A reboot alert contains only one line per stored display
name: `VPS name requires reboot`. A successful run without a required reboot is
silent. Remote Ubuntu hostnames are never used. Reboot checks are read only from
this workflow's fixed **VPS reboot summary** output, after opt-in, with persistent
delivery tracking and retry. Alerts remain off until enabled; saved successful
execution data is required. School failure-alert behavior is unchanged.

### Early club/school closure notices

A clear dated notice that a club or school closes early is an actionable reminder,
not automatically a conflicting reschedule. When the notice supplies only a closing
or collection cutoff (not an opening time), extraction creates an all-day date
marker whose title and notes include that cutoff. It does not invent opening hours,
a timed session or a separate last-day-of-term event from contextual prose. Audience,
source-evidence and duplicate checks still apply; actual cancellations, reschedules,
ambiguous dates and conflicting times continue to need review. The extraction-cache
version is refreshed, so re-forwarding an older reviewed notice after updating can
re-evaluate it without clearing event or duplicate history. Backend-only change:
no workflow re-import or republish is needed.

### Comparing notes on repeat notices

For an already matched calendar occasion, differing saved and incoming notes now
receive a separate OpenAI meaning comparison. Equivalent reminders are kept once,
including repeated wording already accumulated by older versions. New practical
facts are added; a shorter reminder does not remove old details just because it
omits them. An old instruction is replaced/removed only for a clear authoritative
correction, not merely because an email arrived later. Ambiguous contradictions
still need review and keep the existing calendar entry untouched.

Output paragraphs must be exact excerpts of the saved or incoming notes, checked
locally before any calendar writes. Matching dates, times, cohorts and occasions
remains separate. Exact identical/empty note pairs avoid an extra API call; other
successful comparisons are cached privately in the existing SQLite state. Entire
batches are planned before writes, so an unsafe merge cannot partially update a
newsletter. Normal updates preserve history and do not re-import workflows. To
clean old repeated notes, re-forward the latest relevant notice after updating;
this reconciles the same stored event UID instead of creating a new event. Clearing
history is unnecessary. As with event extraction, semantic decisions use AI and
should be checked on the first live notices; source-excerpt validation prevents
unsupported wording but cannot independently prove the model kept every fact.


### Parent action and deadline reminders

Dated requests to return lunch choices, consent/permission forms, RSVP, book tickets,
pay balances or submit information are calendar actions even without an associated
performance or trip on that date. The extractor creates an all-day entry on the
actual due date with an action-based title and submission details in its notes.
A clock cutoff stays in the title/notes rather than becoming an invented session.
Term/menu labels do not override the due date: a November 2026 email requesting a
form by Thursday 3 December for Spring 2027 meals is due on 3 December 2026.

Each entry records whether it is an activity or a deadline. Activity/deadline pairs
cannot merge just because they share a date or parent title. Different tasks due
on the same day remain distinct; repeat reminders of the same task and due date
reuse the existing entry and notes reconciliation. An email saying 'reminder' about
Mufti Day or a club closure still matches that activity, without creating a separate
task. Menu weekday grids, form template blanks, acknowledgements and undated general
advice do not create entries. Year-group, evidence, uncertainty and duplicate guards
continue to apply. Legacy saved entries default to activity. The extraction cache
version changes so previously ignored deadline mail can be re-forwarded after an
update without resetting history, re-importing or republishing the workflow.
