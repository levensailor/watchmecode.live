# Using watchmecode.live

## Interviewers

1. Open [watchmecode.live](https://watchmecode.live) and click **Interviewer sign in with Google**.
2. Your name and avatar appear top right. Click them for **Candidate portal** and **Log out**.
3. In **New session**, type the candidate's name, choose a starter language, and press **Generate PIN**.
4. Share the PIN, then click **Open IDE**.
5. After the interview, press **End** in the portal. The PIN stops working, and the session stays available for review.

The portal groups sessions by candidate name. **Open** loads any session's files and run history, before or after the interview.

### Organizations and integrations

Open **Organization settings** from the account menu to create an organization, add teammates (they can open every session in the org), and issue API keys. Connect [Ashby](integrations/ashby.md) or [ModernLoop](integrations/modernloop.md) there to create sessions automatically for scheduled interviews, and add take-home assessments that Ashby can send.

When you have an organization, **New session** asks which one owns the session; "Personal" keeps it to yourself.

### Take-home assessments

Candidates open their invite link and get a solo IDE with a countdown and a **Submit** button. Their work is submitted when they press Submit or when time runs out, and the results go back to Ashby.

### Recording and playback

1. In the IDE, press **● Record**. A red **REC** badge with a timer appears for you and the candidate, and the candidate gets a notice.
2. Press **■ Stop recording** when you're done. Ending the session also stops recording.
3. Open **Recordings** from the IDE toolbar or from a session in the portal, then press **▶ Play**.

The player rebuilds the workspace as it was when recording started, then replays every edit, file change, and run. Use **1x / 2x / 4x** to change speed, drag the seek bar to jump around, and press Space to play or pause. Recordings are visible only to the interviewer who owns the session.

## Candidates

1. Open [watchmecode.live](https://watchmecode.live). You don't need an account.
2. Type the 6-digit PIN into **Join an interview** and press **Join**.
3. You land in the same IDE as your interviewer.

## The IDE

| Area | What it does |
| --- | --- |
| Explorer | Create, rename, and delete files and folders. Paths with slashes create folders. |
| Tabs and editor | CodeMirror 6 with syntax highlighting for every supported language |
| Run | Runs the active file, or the session's starter entry file. Shortcut: Mod+Enter |
| Live refresh | Re-runs about a second after the last keystroke |
| Output / Preview | Program output, or a sandboxed iframe for HTML/CSS/JS |
| Console | Full run history with commands, stdout, stderr, exit codes, and who ran it |
| Menu | File, Edit, View, Run, and Help expose the same actions |

| Shortcut | Action |
| --- | --- |
| Mod+Enter | Run |
| Mod+S | Save now (edits also autosave) |
| Alt+N | New file |
| Alt+W | Close tab |
| Mod+F | Find |
