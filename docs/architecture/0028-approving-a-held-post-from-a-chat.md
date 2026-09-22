# ADR 0028: Approving a held post from a chat

Status: Accepted and built — `services/api/src/trendrelay_api/approval_notices.py`,
`services/api/src/trendrelay_api/approval_words.py`,
`services/api/src/trendrelay_api/integrations/telegram.py`,
`services/api/src/trendrelay_api/integrations/telegram_setup.py`,
`scripts/worker.py` (`start_telegram_approvals`),
`apps/web/app/campaigns/page.tsx` (the campaign's switch), `docs/third-party/python-telegram-bot.md`.

## Context

Every authority level below `autonomous` holds each frozen post for a person
(ADR 0011, `campaign_runner._hold_reason`). The inbox that person decides in
is a card on the campaign page, and nothing told them it had anything in it.
The approver is usually the one person not at the desk, and the decision is
one look and one press: is this the right clip, does the caption read, does
it go now.

A notification alone would have answered half of it. "Three posts are
waiting" sends somebody to a laptop for a decision they could have made from
the bus stop, and the posts wait for the laptop. The question is not whether
to notify but whether the decision itself can happen where the person is,
under the checks it would meet in the app.

## Decision

**The chat is a second face on the same inbox, not a second inbox.** A press
runs `campaign_runner.approve_execution` or the same dismissal the route
calls, with the same finalisation checks, the same media-hash re-read, the
same delivery block. A post that is not finished is refused from the chat for
the reason it would be refused in the app, and stays held. Nothing about a
held post is decided twice or decided differently depending on where the
thumb was.

**The post travels with the card.** A carousel arrives as an album of its
pictures, then the card; a single picture or a video carries the card as its
caption. Approving what you cannot see is not approving. Pictures are scaled
to a phone before they go, and a video too large for a bot upload is stood in
for by the Library's own still.

**The card speaks the campaign's language.** Its buttons and its outcomes come
from `approval_words`, in the seven languages the interface has, defaulting to
the campaign's post language. The caption is quoted as written.

**A post is announced once, and the card is re-pointed rather than re-sent.**
The announcement used to be remembered by the execution it went out for,
which is the one thing about a held post that does not survive it. A failed
delivery and a dismissal both settle their execution and free the queue item,
and neither stamps the item - so the next minute's plan froze the same post
into the same slot as a new execution, found it held, and sent another card.
One post drew four cards across three hours, two of them after it had already
been approved and the last after somebody had dismissed it.

So the memory belongs to the post rather than to the row that happened to
carry it: `CampaignApprovalNotice` is keyed by the pairing an approver
actually sees - this clip, to this account, in this campaign - and a unique
index on it is what makes a second card impossible rather than merely
unlikely. A post frozen again re-points the card already in the chat at the
execution that now exists, because the card carries an execution id and a
press has to reach a row that is still there; without that, pressing the card
answers "that post is gone" about a post sitting in the inbox. A decision
settles the notice wherever it was made, the app included, so a post that was
answered is never asked about again. Publishing is the only thing that forgets
a notice: the clip returns to the rotation and its next outing is a new
posting decision rather than the same one re-asked.

The overdue follow-up became part of the same rule. It was a second message,
remembered on the execution, so every replaced execution re-armed it - the
repeat problem wearing a different hat. It is now written onto the card that
is already there, above the campaign's name, once per post. One post, one
message, edited as the post's situation changes.

**Long polling in the worker, not a webhook.** A press arrives as an update
that something has to fetch. A webhook needs a public HTTPS address, which a
local install does not have. The worker runs one thread beside its job loop
holding a `getUpdates` request open, so a press is answered in about a
second, and the thread sleeps while the tool is not set up. The update offset
is kept on disk, so a press is carried out once and a restart does not replay
it.

**The chat is the trust boundary, and it is narrow by construction.** The bot
sends only to the one chat saved in Tools; a press from any other chat is
refused. Within it, an optional list of Telegram user ids narrows further.
That is the whole of it, and it is written in the tool's notes rather than
implied.

**The press is the confirmation.** Every outward action in this app carries
`confirm_external_action`, because a request can be made by anything. A
button on a card in a private chat is that same deliberate second act, made
by a person who has just read the post. Demanding a second tap would be
ceremony, not safety.

**The decision is audited as what it is.** The audit event carries the
approver's Telegram id and handle and `via: telegram`. Its `actor_user_id` is
the person who set the campaign's autopilot up, because a Telegram account is
not a workspace member and inventing a mapping would put a name on the record
that no sign-in ever established.

**Off by default, per campaign.** The switch is a campaign setting beside the
rest of how the campaign runs, and it appears only once Telegram is connected
in Tools - a switch that could do nothing is not a choice. A test campaign
never reaches the approver's phone because it was never asked to.

## Consequences

- A workspace that wants this accepts that anyone in the saved chat can
  approve its posts. That is a real widening of who may publish, and it is
  the operator's to choose per campaign, with the approvers list to narrow it.
- A post that keeps failing to deliver is now silent after its first card. It
  is still in the inbox, still re-proposed every pass, and the campaign's run
  note still says what happened - but the chat, which is where the approver
  was, no longer says it. That is the trade the once-per-post rule makes, and
  it is the right way round: four cards asking a question already answered
  twice taught the approver to ignore the chat, which costs more than silence.
- The words cannot be edited from the chat. Rewriting a caption is a keyboard
  job and the link on every card opens the post in the app; the chat carries
  the decision, not the editing.
- The worker must be running for a press to be answered. A card whose buttons
  do nothing is the visible symptom of a stopped worker, which is worth
  knowing.
- A second channel would reuse this shape: the card, the words table, and the
  press handler are the channel-independent parts, and only the transport and
  the trust boundary would be new.
