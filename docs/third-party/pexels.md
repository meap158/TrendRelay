# Pexels

Free stock photos and clips, searched by words and filed into the Library like
any other media.

## Why it is here

Storytelling cuts pictures to a narration. A script about something nobody
filmed has no pictures in the Library to cut to, and Pexels is the answer for
the generic half of that - a city at night, hands typing, a road in the rain.

It is deliberately not the answer for the other half. A dramatised recreation
of a specific event is not stock footage, and using it as though it were puts
a smiling model in a story about something that happened to somebody.

## What it needs

One API key, free from <https://www.pexels.com/api/>, saved on the Pexels card
in Tools. There is nothing to install: the key is the switch.

## What it does

- **Search** photos or clips by words, asked in the shape the video is being
  rendered at, so a candidate does not arrive to be cropped in half.
- **Preview** before anything is downloaded. A search returns a thumbnail and a
  credit and no download link.
- **Import** through the Library's own ingest, so b-roll is hashed,
  de-duplicated, thumbnailed and searchable like everything else - and a
  narration plans over Library assets whatever they came from.

## Attribution

The [Pexels licence](https://www.pexels.com/license/) asks for the photographer
to be credited where the media is shown. The credit is carried on every search
result, drawn on the tile in the picker, and stored on the imported asset -
rather than being something a person has to remember to add later.

Two things the licence does not allow, which nothing here does automatically
and which are worth knowing before publishing: media may not be sold
unaltered, and identifiable people in it may not be shown in a way that implies
endorsement of a product.

## What is fetched

Photos at `large2x` rather than the original, because an original can be a
forty-megapixel print scan and nothing here renders above 1080 lines. Clips at
the largest file at or under 1080 lines, for the same reason in the other
direction: anything smaller would be upscaled into a narration.
