# Goal Tracker: User Guide

A simple guide to every feature of Goal Tracker: what it does, how to use it,
and where the local AI model helps.

---

## 1. What is Goal Tracker?

Goal Tracker helps you:

- **plan** weekly and monthly goals, broken into small milestones,
- **record** what you did each day,
- **see** how you are doing,
- **write** your daily status mail,
- **review** completed weeks and months in saved reports,
- **ask Jarvis**, your assistant, about your records and let it make changes
  for you (only after you confirm).

Everything runs **on your own computer**. Your data is stored locally, and the
AI model also runs locally through **Ollama**. Nothing is sent to the internet.

There are two ways to use it:

| Way | What it is |
|---|---|
| **Web app** (recommended) | Pages in your browser at `http://127.0.0.1:8000`, with all features |
| **Terminal app** | The original menu in the command window; still works as before |

Both use the same data, so you can switch between them at any time.

---

## 2. Getting started

### What you need

- **Python 3.12** and **uv**
- **Ollama** with the model `qwen3:4b-instruct` (only needed for AI features;
  everything else works without it)

### First-time setup

Run these in the project folder:

```powershell
uv sync --locked
Copy-Item -LiteralPath .env.example -Destination .env
ollama pull qwen3:4b-instruct
```

### Start the web app

```powershell
uv run python -c "import uvicorn; from goal_tracker.web.app import create_app; uvicorn.run(create_app('.env'), host='127.0.0.1', port=8000)"
```

Wait until it says *Application startup complete* (it can take up to about 40
seconds), then open **http://127.0.0.1:8000** in your browser. Keep the window
open while you use the app; closing it stops the app.

### Start the terminal app

```powershell
uv run goal-tracker start --env-file .env
```

Choose an option by typing its number: create weekly or monthly goals, record
daily progress, manage goals, view the progress summary, view or export
reports, run diagnostics, or 0 to exit.

### Make sure the AI is ready

Ollama must be running for AI features. To check:

```powershell
uv run goal-tracker doctor --env-file .env --llm
```

---

## 3. Finding your way around the web app

**The top bar** (on every page):

| Item | Where it takes you |
|---|---|
| **Goal Tracker** (logo) | The dashboard (home page) |
| **Goals** | Your weekly and monthly goals |
| **Week plan** | Your Monday-to-Friday plan |
| **Progress** | Record and review daily progress |
| **Summary** | How a week or month is going |
| **Reports** | Saved reports for completed periods |
| **Daily mail** (purple button, top right) | Write your daily status mail |
| **Sun / moon button** (far right) | Switch between light and dark mode |

**The round robot button** in the bottom-right corner opens **Jarvis**, your
assistant (see section 11). A red number on it means Jarvis has suggestions
waiting.

**The footer** at the bottom of each page shows "Atharv ji ki Jai Ho !!".

### Look and feel

- **Light and dark mode:** click the sun/moon button. Your choice is
  remembered. Until you choose, the app follows your Windows setting.
- **Animations:** pages and cards fade in, cards lift when you point at them,
  and buttons ripple when clicked.
- **Cursor effects:** a neon trail follows your mouse. Glowing corner brackets
  follow the pointer and **lock onto** any link, button or text box you point
  at, labelled LINK, INPUT or TARGET LOCKED. Clicking sends out a hexagon
  shockwave. Your normal cursor stays as it is.
- Effects switch off on touch screens and when Windows animations are turned
  off.

---

## 4. Dashboard (home page)

The dashboard gives you a quick picture of today:

- this week's **plan completion** (with a progress bar),
- your number of **active goals**,
- how many work items are **pending or in progress**,
- how many **days you logged** this week,
- reminders for **completed periods without a report**,
- a **Jarvis** card showing how many suggestions are waiting,
- shortcut links: create a goal, plan your week, record progress, write
  today's mail, view the summary, open reports.

---

## 5. Goals

A **goal** belongs to one **week** (Monday to Sunday) or one **month**. Each
goal has a title, an optional description and expected outcome, an optional
deadline, and a checklist of **milestones**: small results you can tick off.

### Create a goal

1. Go to **Goals → Create a goal**.
2. Choose **weekly** or **monthly** and the start date.
3. Enter a title and, if you like, a description, expected outcome and deadline.
4. Write your milestones, one per line.
5. Click **Review goal →**, check everything, then **Confirm and save**.

> **AI helps — milestone ideas:** click **Suggest milestones with AI** before reviewing.
> The local model reads your title and description and adds ideas to the
> milestone box. Edit or delete any line; nothing is saved until you confirm.

### AI suggestions for a saved goal

After you save a goal, its page opens and shows an **AI suggestions** section:

- **Milestones to consider:** milestones missing from your plan. Each has an
  **Add this milestone** button that opens the normal review page, so you
  confirm before anything changes.
- **Tips:** short, practical advice for this goal.
- **Risks to watch:** what could stop you, and how to avoid it.

> **AI helps:** the local model reviews the goal once in the background (about
> a minute). The suggestions are saved, so they don't need to be made again. If
> you edit the goal later, the page says the suggestions are out of date and
> offers **Regenerate suggestions**.

### Manage goals

On a goal's page you can:

- **Edit** the title, description, expected outcome or deadline. You give a
  reason, and the old version is kept in the history.
- **Cancel** an active goal or **archive** a finished one. The records are
  kept.
- **Finalize the initial plan** for the week or month. Goals added after that
  are marked as "added after initial planning", so your original plan and
  later changes can be compared fairly.

### The "Independent tasks" goal

Work that is not related to any of your goals is collected automatically in one
shared **Independent tasks** goal per week. It is marked **Independent** and
**never counts towards completion**, so extra work never lowers your
percentages.

---

## 6. Week plan (Monday to Friday)

**Week plan** spreads your goals' open milestones and pending work across the
**working days, Monday to Friday**.

1. Open **Week plan**. The bottom of the page shows **what the plan uses**:
   your goals with open milestones and your pending work.
2. Click **Plan my week**.
3. You get one card per day with a **focus** and a short **task list**. Today
   is highlighted, and there are a few notes for the week.

- During the week it plans from **today to Friday**. On Saturday or Sunday it
  plans the **coming week**. **Plan next week →** lets you plan ahead.
- The plan is saved for the week. If your goals or pending work change, the
  page tells you and offers **Regenerate plan**.

> **AI helps:** the local model decides the order, putting earlier deadlines
> first and continuing unfinished work early in the week. It uses only your own
> milestones and pending work. The plan is advice only; nothing is saved as
> progress.

---

## 7. Daily progress

Record what you did each day: **work** (completed, in progress or pending),
**learnings**, **challenges**, **observations** and **achievements**.

### Two ways to record

**A. Write it in your own words (AI-reviewed)**

1. Go to **Progress → Record progress**.
2. Write your day naturally, for example: "Finished the PDF export, 45 min.
   Learned about retrievers. Login bug still pending."
3. Choose **With AI — propose items from my journal for me to review**. The
   local model splits your text into separate items.
4. **Review every item**: fix the wording, status, minutes and linked goals.
5. Save.

> **AI helps:** the model sorts your text into work, learnings, challenges and
> so on, and keeps an exact quote from your text as evidence for each item. It
> never marks something completed unless you said so, and you review
> everything before saving.

**B. Enter items yourself:** choose **Manually — I will add each item myself**
and fill in the items one by one, without AI.

### Linking work to goals

For each work item the app asks **"Is this task related to any of the listed
goals?"**

- **Related to a listed goal:** tick the goal(s), and optionally the milestones
  the work completes.
- **Independent task:** it goes to the week's **Independent tasks** goal and
  doesn't affect your scores. For weeks that have already ended, it is saved
  without a goal.

### Pending work and history

- **Pending** lists everything still pending or in progress. Use **update** to
  mark an item completed or in progress later (**Update →**), which links the new status to
  the original item.
- **Entry history** shows past entries. Corrections keep the original record.
- Saving is protected against double clicks, so the same entry is never saved
  twice.

---

## 8. Daily mail

Click the purple **Daily mail** button (top right). The mail uses your
template, with these sections:

1. Meetings / Activities
2. Use Cases / Activities Completed
3. Learning
4. Plan for Tomorrow
5. Blockers / Support Required

### Write today's mail

1. Answer one short question per section. Rough notes are fine; leave a
   section empty if it doesn't apply.
2. Click **Write my mail**.
3. Check the result. You can **edit the subject and body** right on the page.
4. Send it (see below), or click **Copy subject and body**.

> **AI helps — rephrasing:** the local model rephrases your notes into clear,
> professional bullet points **without changing their meaning**. It fixes
> spelling and grammar, makes one point per bullet, uses past tense for work
> done and future tense for plans, and keeps technical names exactly as you
> wrote them. It never adds tasks, names or numbers you didn't write, and an
> empty section stays empty. If the model isn't available, your own words are
> used.

**Save as progress too:** **Review and save as progress →** turns your answers
into a progress entry through the normal AI review, so you don't type your day
twice.

### Mail from saved progress

**Fill from saved progress instead** (on the Daily mail page), or **Create mail
for this day** on a saved entry, fills the same template automatically from
progress you already saved for the day. This needs no AI.

### Sending

1. Click **Open in Outlook**. A new message opens in Outlook with the
   recipient, subject and body filled in.
2. Check it and press **Send** in Outlook.

- The recipient comes from `GT_MAIL_TO` in your `.env` file (and `GT_MAIL_CC`
  for copies). Restart the app after changing it.
- **Sent mails** lists every mail opened from the app. If you already sent
  today's mail, the page warns you before you send another.
- The app never sends mail by itself; you always press Send.

### Your own template

The template is a text file with placeholders such as `{{goal_tasks}}`. The
first line can be `Subject: ...`. To use your own file, set
`GT_DAILY_MAIL_TEMPLATE` in `.env`. Unknown placeholders are listed as a
warning.

---

## 9. Summary

**Summary** shows how any week or month is going:

- **completion** of your original plan, and of the plan including later changes,
- goals and milestones, with **overdue** ones flagged,
- how consistently you logged progress,
- time recorded,
- a day-by-day trend.

The numbers come straight from your records. The AI does not calculate them.

---

## 10. Reports

When a week or month is over, save a **report**: a snapshot that never changes
afterwards.

1. Go to **Reports**, pick a completed period and click **Generate report**.
2. Optionally include **AI advice**. You preview it before saving.
3. Save, then view it any time or **download it as a PDF**.

> **AI helps — advice:** the local model suggests areas for improvement, ideas
> for the next period and possible future goals. Every suggestion shows the
> **evidence** (your records) it is based on, and you can save a factual report
> without AI.

---

## 11. Jarvis, your assistant

Click the **robot button** in the bottom-right corner on any page.

### The Jarvis character

Jarvis is an animated robot face. It **blinks**, floats gently and **follows
your mouse with its eyes**. It tilts its head to say hello when you open it,
"talks" and flashes its antenna while thinking, and bounces happily when it
answers.

### Ask questions

Type a question and press **Enter**. For example:

- "What are my goals this week?"
- "What is still pending?"
- "What did I learn this week?"
- "What did I do yesterday?"

Under each answer, **Based on N saved records** shows exactly which records
were used.

> **AI helps — answers from your records only:** the local model answers using
> only your saved goals, progress and pending work. If something isn't in your
> records, or the question isn't about them (for example "what's the capital of
> France?"), Jarvis says **"I couldn't find anything about that in your saved
> records"** instead of guessing. Answers that don't point to a record are
> thrown away by the app.

### Ask Jarvis to do things

You can also tell Jarvis to make a change:

| Say something like | What Jarvis prepares |
|---|---|
| "Mark the vector store as done" | Update pending work to completed |
| "I finished the deploy milestone" | Mark a milestone done |
| "Log 30 minutes of code review today" | Record new work (an independent task if no goal is named) |
| "Add milestone 'Write docs' to my LangChain goal" | Add a milestone to a goal |
| "Move the deploy milestone to Thursday" | Change a milestone's due date |

Jarvis shows a **card** describing the change. **Nothing is saved until you
click Confirm.** Click **Dismiss** to cancel.

> **AI helps — understanding commands:** the local model works out what you
> want and which record you mean. The app then checks it carefully: the record
> exists, the dates are valid and inside the goal's period, and the milestone
> isn't already done. If the model points at the wrong record, the app matches
> your words to your records, and refuses when it isn't sure. Confirming twice
> never saves twice.

- **Clear** starts a new conversation. The chat is kept while you move between
  pages and cleared when you close the tab.
- **Suggestions** opens the Jarvis suggestions page.

---

## 12. Jarvis suggestions (agents)

Jarvis also prepares suggestions **on its own**. See them under **Suggestions**
in the Jarvis panel, the **Jarvis** card on the dashboard, or the page
`http://127.0.0.1:8000/agent`. Every suggestion has **Confirm** and
**Dismiss**; nothing changes until you confirm.

### Follow-ups (rules only, no AI)

- **Old pending work:** anything pending or in progress for 3 or more days gets
  "…has been pending for N days. Done?" Dismiss means you're still working on
  it; it asks again at most once a week.
- **Deadlines:** milestones that are overdue or due by the next working day get
  "Already done?". Overdue ones also get "Move the due date to the next working
  day", skipping weekends.

### Friday wrap-up

- **Carry-over:** proposes carrying each unfinished weekly goal to next week as
  "<goal> (carried over)" with its open milestones.
- **Your week in review:** a short recap. **AI helps:** the local model writes
  it from your records only. Click **Got it** to clear it.

### Monday planning

- **Last week's report:** proposes saving it with AI advice.
- **New goals:** proposes up to 3 goals for this week from that advice, each
  with suggested milestones. **AI helps:** the advice and milestones.
- **Week plan:** prepares this week's Monday-to-Friday plan if there isn't one
  yet.

### When suggestions are prepared

While the web app is open:

| Job | When it runs |
|---|---|
| Follow-ups | Weekdays from 18:00 |
| Friday wrap-up | Friday from 17:00 (or at the weekend if the app was closed) |
| Monday planning | Monday from 09:00 (or until Wednesday if the app was closed) |

- Each job runs **once per slot**. If Ollama isn't running, it tries again
  later.
- To save memory, Jarvis never runs two AI tasks at the same time.
- You can run any job straight away with the **Run now** buttons on the
  suggestions page.
- To turn scheduled runs off, set `GT_AGENT_SCHEDULE=false` in `.env`.

---

## 13. Where the local AI model helps: at a glance

| Feature | What the local model does | Your control |
|---|---|---|
| Suggest milestones (new goal) | Proposes milestone ideas | Edit before reviewing and saving |
| AI suggestions (saved goal) | Missing milestones, tips, risks | Add a milestone only through review |
| Week plan | Spreads work over Monday–Friday | Advice only; never saved as progress |
| Record progress (AI) | Splits your text into items, with quotes | Review every item before saving |
| Daily mail | Rephrases your notes professionally, same meaning | Edit, then press Send yourself |
| Reports | Advice with evidence | Preview; a factual report needs no AI |
| Jarvis questions | Answers from your records only | Shows the records used; refuses otherwise |
| Jarvis actions | Understands commands | Confirm or dismiss every change |
| Weekly agents | Week recap, report advice, goal ideas, plan | Confirm or dismiss every suggestion |

**Features that need no AI:** creating and editing goals by hand, manual
progress, the summary, factual reports and PDFs, mail from saved progress, and
follow-ups.

**Privacy:** the model runs on your computer through Ollama. Your goals, notes
and mails never leave your machine.

---

## 14. Settings (`.env` file)

| Setting | What it does |
|---|---|
| `GT_LLM_MODEL` | The local model to use (default `qwen3:4b-instruct`) |
| `GT_OLLAMA_BASE_URL` | Where Ollama runs (default `http://127.0.0.1:11434`) |
| `GT_LLM_CONTEXT_LENGTH` | How much text the model reads at once (default 8192; keep it, or the model may need too much memory) |
| `GT_LLM_TIMEOUT_SECONDS` | How long to wait for the model |
| `GT_TIMEZONE` | Your timezone (default `Asia/Kolkata`) |
| `GT_MAIL_TO` / `GT_MAIL_CC` | Who receives the daily mail (separate several with `;`) |
| `GT_MAIL_SEND_MODE` | `open` (default: opens Outlook for you to send) |
| `GT_DAILY_MAIL_TEMPLATE` | Path to your own mail template |
| `GT_AGENT_SCHEDULE` | `true` / `false`: Jarvis scheduled suggestions |

Restart the app after changing settings.

---

## 15. Your data and backups

- Your data is stored in `%LOCALAPPDATA%\GoalTracker` (`tracker.sqlite3`,
  with `logs`, `backups` and `reports` folders).
- A backup is made automatically before every database upgrade.
- To make a backup yourself:

  ```powershell
  uv run goal-tracker db backup --env-file .env
  ```

- History is kept: edits, corrections and cancellations never delete the old
  records.

---

## 16. Troubleshooting

| Problem | What to do |
|---|---|
| The page doesn't open | Start the web app (section 2) and wait up to 40 seconds |
| "The local model is unavailable" | Start Ollama, then try again; check with `uv run goal-tracker doctor --env-file .env --llm` |
| AI is slow | Normal on this machine: each AI task can take up to a minute |
| The app stopped by itself | The computer may be low on memory. Close heavy programs and start the app again |
| Out-of-memory error from the model | Keep `GT_LLM_CONTEXT_LENGTH=8192` in `.env` |
| "Open in Outlook" does nothing | Make sure Outlook is your default mail app, or use **Copy subject and body** |
| The mail body is cut off in Outlook | Very long mails can be cut; use **Copy** and paste instead |
| A Jarvis card says "Couldn't save" | The record changed in the meantime; ask Jarvis again or use the normal page |
| Jarvis says it couldn't match a record | Use the words from the goal, milestone or task name |
