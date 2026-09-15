# python-telegram-bot — approval requests on Telegram

**Repository:** https://github.com/python-telegram-bot/python-telegram-bot
**Licence:** LGPL-3.0 — commercial use allowed; the library is used unmodified,
as a separately installed package, which is what the LGPL asks.
**Adopted for:** telling the approver, on Telegram, that a campaign post is
waiting for them.
**Surface:** `campaigns` · **Runs:** network · **Pinned:** 22.8

## Why it is here

A campaign run by exception holds every post until a person approves it. The
held post sits in the campaign's approval inbox until somebody opens the app,
and the approver is usually the one person not at the desk. The approval
itself is one look and one press. Telegram is where that person already is,
so each held post goes there as a card with the inbox's own buttons under
it, and a press decides it - the way a card is swiped.

It is optional, and campaign by campaign: the switch is a campaign setting,
ruled off at the end of "How it posts" in both the new-campaign form and the
campaign's settings dialog, and it says which bot and which chat beside it.
It appears only once the tool is connected here - a switch that could do
nothing is not a choice - and stays on the form while it is already on, so
it can always be switched off. Turning the tool on does not, by itself, make
any campaign send; switching a campaign on sends the posts it is holding at
that moment, and every hold after. The approval inbox says when its posts are
also on Telegram.

## What it needs

1. **The library**, installed from the Telegram card in Tools. It goes into a
   runtime of its own (`.tools/telegram-bot/runtime`), the way yt-dlp does,
   so the API's environment carries nothing for a machine that never sends.
2. **A bot token.** In Telegram, open **@BotFather**, send `/newbot`, answer
   its two questions, and paste the token it gives you into the card. The
   token reads like `123456789:AbCdEfGh...`.
3. **A chat id.** Where the messages go. A bot cannot start a conversation,
   so first either write anything to your bot, or add it to a group. Then
   read the id: open `https://api.telegram.org/bot<token>/getUpdates` in a
   browser and take `chat.id` from the newest entry. A private chat's id is
   your own user id; a group's starts with a minus sign. A public channel can
   be named as `@channelname` instead, if the bot is one of its admins.
4. **Approvers**, optionally: the Telegram user ids allowed to press the
   buttons, comma-separated. Your own id is in the same `getUpdates` reply,
   under `from.id`. Leave it empty in a private chat.
5. **Send a test carousel** from the card. It checks the token with Telegram
   (`getMe`) and sends what a held carousel looks like - an album of three
   sample pictures, then the card with its buttons, which on the test only
   answer - so a wrong token and a wrong chat are told apart before a real
   post is held, and the eye learns where the pictures sit.

The values are saved to this machine's `.env` under `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID` and `TELEGRAM_APPROVER_IDS`; the token is masked in the
interface afterwards and never returned to the browser.

## What is sent: one card per held post

When the campaign runner holds posts for approval in a planning pass, and the
campaign has asked for Telegram, each held post goes to the chat as it will
look: the media first, then the card - the campaign, the account, when it is
due, the caption, and why it was held - with the same choices the app's
approval inbox offers under it:

| Button | What it does |
| --- | --- |
| ✅ Approve | `approve_execution` - the post is queued exactly as frozen, to go out at its slot |
| 🚀 Approve and post now | the same, with `publish_now` - delivered at once |
| 🚫 Dismiss | the execution is cancelled, freeing its slot and its queue item |
| ↗ Open in app | a link to the campaign's inbox, for anything the card cannot do (editing the words) |

The last one appears only when `PUBLIC_WEB_URL` is an address a phone could
open. Telegram refuses a button pointing at `localhost` or a private address,
and refuses the message carrying it, so the button is left off rather than
risked; the Telegram card in Tools says which of the two you have. Approving
and dismissing work either way.

**The language.** The card's own words - its buttons, what it says once
decided, why a press was refused, the due date - are written in the
campaign's post language, in any of the seven the interface speaks; a
campaign can choose another for its cards in the same setting, for the
approver who reads a different language from the audience. The post itself
is quoted as written. Refusals that arrive before the post is known - a
press from another chat, a button that is not ours - are English.

**The media.** A carousel arrives as an album of its pictures (up to ten,
Telegram's limit), followed by the card, because an album cannot carry
buttons. A single picture, or a video, carries the card as its caption with
the buttons under it, when the card fits Telegram's caption limit; otherwise
the card follows as its own message. Pictures are scaled to a phone's
screen with ffmpeg before they go - a Library original is often a
multi-megabyte PNG - and a video over the fifty-megabyte upload limit is
stood in for by the Library's still of it. A picture that cannot be prepared
is left off and named on the run's note; the card still goes, because the
words and the buttons are what decide.

A press rewrites the card to say what was decided and by whom - "✅ Approved
by @ana" - and removes the buttons, so a decided post cannot be pressed twice
and reads as decided. A press on a post that was meanwhile decided in the app
answers with the fact ("Already decided in the app: it is queued") and changes
nothing. Past eight held posts in one pass the rest are one line with a count
and the link; the inbox lists them all.

A send that fails is written on the run's note and does not fail the run: the
post is still held in the app, which is the thing that matters. Nothing is
sent for a campaign that runs autonomously: it holds nothing.

## How a press reaches the app

The worker runs a thread beside its job loop that long-polls Telegram for
button presses (`getUpdates`, presses only, held open for twenty-five seconds
at a time) and carries each one out through the same functions the inbox's
routes call - `campaign_runner.approve_execution`, the same dismissal - so a
post decided from the chat meets the same checks it would meet in the app: a
post that is not finished, or whose media changed under it, is refused with
that reason as the toast, and stays held. The update offset is kept in
`.data/telegram/state.json`, so a press is handled once and a restart does
not replay it. While the tool is not set up the thread sleeps and asks
Telegram nothing.

Long polling rather than a webhook because a local install has no public
HTTPS address to be called on, and a thread rather than a durable job because
the poll waits rather than works.

## Who may press

Two checks, and they are the whole of the trust boundary, so they are written
down:

1. **The chat.** The bot sends only to the chat saved on the card, and a press
   from any other chat is refused. In a private chat that is the operator
   alone; in a group it is everyone in the group.
2. **The approvers list**, optional. Telegram user ids saved on the card; when
   the list is not empty, only those may press. Use it for a group where not
   everyone should decide.

The approver's Telegram id and handle are written on the audit event beside
the decision (`via: telegram`), and the event is recorded against the person
who set the campaign's autopilot up, since a Telegram account is not a
workspace member. The press is the confirmation: what `confirm_external_action`
asks of a request in the app, the button asks of the thumb.

## Privacy

Each card carries the caption and the campaign's name to a chat the operator
chose. Nothing else leaves: no media, no credentials, no audience data. The
bot token is the only secret, and it is the one `@BotFather` can revoke in a
moment - revoking it stops both the cards and the presses.
