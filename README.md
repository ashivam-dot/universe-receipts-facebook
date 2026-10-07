# Universe Receipts → Facebook Reels

Posts Universe Receipts Shorts to the [Universe Receipts Facebook Page](https://www.facebook.com/profile.php?id=61595101173821)
as Reels. It is separate from the studio: it reads `ashivam-dot/universe-receipts` with a read-only deploy key
and never writes to it, Buffer, Modal, or Cloudinary.

## How it works

- **Store** (every 4 hours): for each Short the studio has sent to YouTube, download its video while the studio
  still hosts it (it frees the copy 2 days after going live) and keep it as an asset of the `videos` release here.
  The library (`state/library.json`) holds each Short's title, caption, YouTube link, and views.
- **Post** (12:30 and 19:30 New York): publish one Reel through the Graph API, at most `FB_PER_DAY` (2) a day.
  It picks the unposted Short with the most YouTube views per day that has been live at least 24 hours, so the
  Page gets the channel's best work first. Results go to `state/posted.json`; a Short is tried at most 3 times.
- **Caption:** title, description, sources by site, "Narrated with a synthetic voice.", and up to 3 hashtags.
  The Graph API has no "AI info" switch, so the disclosure lives in the caption.

## Secrets

- `SOURCE_DEPLOY_KEY`: private half of the read-only deploy key `facebook-reader` on `universe-receipts`.
- `FB_PAGE_TOKEN`: a long-lived Page access token for the Page (pages_manage_posts, pages_read_engagement,
  pages_show_list) from the Meta app owned by the Page's admin. It doesn't expire unless the password changes
  or the app loses access; the run fails loudly when it does.

## Commands

- `python3 fbposter.py status`: what is stored and posted.
- `python3 fbposter.py backfill` (on the Mac, with `SOURCE_DIR` pointing at a fresh clone and `GH_TOKEN` set):
  fetch older Shorts from YouTube with yt-dlp when their hosted copy is already gone.
- Run now: `gh workflow run facebook -f post=true`.
- Pause: disable the `facebook` workflow in Actions.
