# Video generation

Hosted video from a prompt and a Library image. TrendRelay does not keep
its own copy of the vendor list in the page. The Tools card, Attribution
Generate, and the Library editing row all read one registry.

## What it needs

Each provider is a key and a switch, saved on the Video generation card in
Tools. There is nothing to install.

A provider is offered on a video draft only when all of these hold:

- the switch is on
- a key is saved
- Check has accepted that key
- any extra requirement that provider declares is met

Check calls a models endpoint. It does not start a video, so it does not
spend a generation. A key that is only present is not treated as working.

Several providers can be ready at once. The draft lists each one by the
label the registry gives it, and the operator picks one. A refusal does not
fall through to another provider.

The first two entries:

- **xAI.** `XAI_API_KEY`, the same name Last 30 Days already reads. This
  account keeps zero data retention, so the video is written to a presigned
  address in the Cloudflare R2 bucket already set on Publish, under
  `product-clips/`. A clip started from a Library image is written under
  `library-clips/` instead. The card does not ask for those hosting secrets again.
  Region is `auto`. The endpoint is
  `https://<account>.r2.cloudflarestorage.com`. The model is
  `grok-imagine-video-1.5`, a 10 second vertical clip. The Library image is
  sent as a reference, not as a locked first frame. The worker runs
  one generation at a time.
- **Gemini.** `GEMINI_API_KEY`, from Google AI Studio. No bucket. The model
  is `veo-3.1-generate-preview`, an 8 second vertical clip, image to video.
  Video on that API is paid. Check proves the key is accepted. It does not
  prove the project can spend on video. A later refusal is shown on the
  draft and the draft stays pending.

Generation is a job. The API worker has to be running, and it does not
hot-reload, so it needs a restart after this tool is added before it will
claim the job. The card does not start the worker.

Every generation asks for confirmation. The call is metered and leaves the
machine.

## Where it shows up

Two places, and only when at least one provider is ready. The control is
absent when nobody is ready.

Attribution → Generate, on one open video draft that still owes a file
(`mannequin_transition` or `mirror_selfie`). That is the same moment the
file picker is there. The picker stays, so a clip made outside can still
be filed. The draft already holds the prompt. The control is also absent
on image and carousel drafts, and while several products are selected and
each would need its own file. The finished file goes through the draft's
own Library ingest and links to the product when the draft's card count
is met.

Library → the editing row, on an image, as Generate. The operator writes
the prompt in that dialog. It is not filled in from a recipe. Confirmation
is required. One generation is in flight for that image; a second request
returns the same job and does not start the provider it names. The finished
file is a new Library video and is not linked to a product. The button is
disabled when the selected asset is not an image. This is not an effect in
the effect stack, and it is not an OpenMontage clip plan.

Storytelling's "Make the video" still assembles stills, voice, and stock
on this machine. Nothing is published.

## When a generation is refused

The draft shows the service's message and stops. There is no second
attempt, no rewritten prompt, and no cropping of the subject. Auth, quota,
moderation, and unavailable are the four names, and the service's own
words stay with them.
