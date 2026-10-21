# Carousel styles in the publish flow, and lookbook scope (PM-209 and PM-228, DRAFT)

## PM-209: choose a carousel style per draft
FrameMotion now renders a text script into carousel slides in a registered style (`src/lib/carousel-styles.ts`; styles
`stick-man`, `big-centre`, `subtitle`; sizes `portrait` 1080x1350 and `story` 1080x1920; CLI `npm run carousel -- script.txt --style
<id> --size <size> --out <dir>`; output one SVG per slide).

Integration plan for TrendRelay (not built; the web app has uncommitted changes from another session):
1. A draft gets a `carouselStyle` field (`stick-man | big-centre | subtitle`) and a `slideSize` (`portrait | story`), stored with the
   draft, default `big-centre` and `portrait`.
2. The publish flow calls FrameMotion through its CLI (a subprocess with an argument array, never a shell string) so TrendRelay
   needs no import of FrameMotion's code and no new dependency; the output folder is read back as the slide list.
3. SVG slides are rasterised to PNG for upload (headless Chrome or `sharp`, whichever TrendRelay already uses).
4. A style picker in the draft editor previews the first slide.
5. Test: fake the CLI, check the style is stored and passed through; check an unknown style is refused.
Open decision: CLI call versus a shared package. Recommended: CLI first.

## PM-228: lookbooks and "together" outfits
Scope proposal (to confirm with the owner):
- **Lookbook** = an ordered carousel presenting one theme, with a cover slide, one slide per look and a closing call to action.
- **Together** = a slide showing 2-4 of our products styled as one outfit or set, with each product labelled and linked to its
  listing.
- Both are carousel templates in FrameMotion's style registry, filled from TrendRelay's product catalogue (our own products only).
- Reference pages (Instagram: rilly_emulie_official, anmille.official, hemell_official) inform structure and style as data only; see
  `reference-catalogue.md`. If "pribng" in the original request meant pricing, add a price-positioning note per reference page.
Open: which lookbook themes to publish first; whether prices appear on slides.
