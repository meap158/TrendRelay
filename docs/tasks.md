# Tasks

Operator-requested work that is noted but not yet built. One bullet per task;
remove it when the work lands, and name the commit that did.

- **Effect editor: move an object from the object itself.** Clicking a placed
  object in the viewport (not its entry in the left catalog) should offer a
  small popup of actions, and moving the object should be one of them - so
  repositioning is done where the object is, rather than through the catalog.
  Requested 2026-09-03.
- **Approvals over Telegram, optionally, per campaign.** Evaluate and build an
  optional Telegram channel for the campaign approval process: when a post is
  waiting for approval, send it to a configured Telegram chat, and let the
  approval be given from there where that is sound. Backed by
  `python-telegram-bot` (https://github.com/python-telegram-bot/python-telegram-bot),
  registered as a tool in Tools with its setup - bot token, chat - done from
  there. Requested 2026-09-14.
