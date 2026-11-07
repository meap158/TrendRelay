# Mây Meo Grok video batch, 8 November 2026

The owner requested more Grok videos for Mây Meo. Completed 12 existing
Hà My video drafts across eight products and seven recipes: 11 new generation
requests and one recovered studio-turn output. Recovery avoided another paid
request for an already completed file.

All 12 drafts now report `succeeded`, zero files owed, a Library asset ID and
complete product linking. Read each Library record to verify the product link,
video type and `grok`, `ai-generated`, and `ai-video` labels. All 12 source
hashes are distinct. Every file decodes as 720 by 1280 and lasts about ten
seconds. Sampled four frames per clip against its garment reference.

| Product | Recipe | Library asset |
| --- | --- | --- |
| CHOCO | Hanger reveal | asset_df8dae9ffd5843a5a95354e9d262f065 |
| BABYPINK | Bed lounge | asset_0b9ce6debd6f41f79a04610c020a7624 |
| MIU | Studio turn, recovered | asset_392ba67403bb43568508fb906f097e18 |
| BLUEBELL | Held garment | asset_56eaaf985cf54900b66dd838445f3594 |
| BELLE grey | Fabric detail | asset_d346d131b5974a0bab0eee2bd8d17a2f |
| BLUEBELL | Flat-lay reveal | asset_199512f483b74e71a7f38fca51c1163d |
| MIU | Model walk | asset_ce3b5f3eae9d43af8b1c98a69dbaa69e |
| ICY | Flat-lay reveal, review needed | asset_996289d04a9b4cb7a92535b3fdbfb41c |
| CLOUDY | Bed lounge | asset_e1177f9cb1584fb1ac9f294849dcc003 |
| GREY | Hanger reveal, unsuitable garment | asset_4e2c6837a60e48b1827b6fd6404e8ae4 |
| BABYPINK | Fabric detail | asset_896be8e7dc4a4e099492f93b70047f0f |
| MIU | Bed lounge | asset_7782c5b5995246bf8e8f0c38d727c2dc |

Two outputs need attention before any posting. ICY adds gathering at the
neckline relative to the supplied photograph. GREY changes the long-sleeve
top to a sleeveless top and should not represent the product. Added these
observations to each asset's Library analyst notes through the app API.
Generation completion does not mean publication approval. The other ten
clips had no comparable garment change in the sampled frames; this was a
frame-sampling review, not a full audio or motion review.

Used the stored prompts, existing garment subjects and Hà My identity
references through `scripts/fill_creative_videos.py`, one request at a time.
The sandbox could not read the configured Grok credential; the authorized
command succeeded with the required filesystem access. No credentials were
printed. No API or worker was started or restarted.

The videos are filed in Library and linked to their Attribution products.
No campaign posts, approvals, pins or publication settings were changed.
Local files, contact sheets and validation are under
`.data/creative-videos/nov08-review/`. The batch receipt is `validation.json`.
