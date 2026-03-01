---
name: pushmemory
description: Save important information to the project's MEMORY.md file for future reference
---

The user wants to save important information to the project memory file.

1. Read the current contents of `/home/flipper/sheet_music_app/MEMORY.md` (create it if it doesn't exist)
2. If the user provided specific information via $ARGUMENTS, add that information
3. If no arguments were provided, review the current conversation and identify the most important information worth remembering — such as:
   - Decisions made
   - Bugs found and how they were fixed
   - Architecture or design choices
   - Configuration details (ports, paths, interpreter settings)
   - Useful commands or workflows discovered
4. Append the new information to MEMORY.md under a dated heading (`## YYYY-MM-DD`) if one doesn't already exist for today, otherwise add under the existing date heading
5. Keep entries concise — use bullet points
6. Do not duplicate information that is already in MEMORY.md
7. Confirm to the user what was saved
