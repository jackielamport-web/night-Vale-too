# Lights out

A bedtime player for Welcome to Night Vale. It skips the host intro, the weather, the host outro and ad breaks, and has a sleep timer that fades out.

Each day a GitHub Action checks the feed, transcribes new episodes with Whisper, asks Claude where the skippable parts are, and saves the timestamps to `data/episodes.json`. The page on GitHub Pages streams the audio straight from the podcast host and jumps over those parts.

## Setup

1. **Create a repo** and push these files to `main`.
2. **Add the feed URL.** On welcometonightvale.com, copy the RSS link. In the repo, go to Settings → Secrets and variables → Actions → Variables, and add `FEED_URL` with that link.
3. **Add your API key.** In the same place, under Secrets, add `ANTHROPIC_API_KEY`.
4. **Turn on Pages.** Go to Settings → Pages, and deploy from the `main` branch, `/ (root)`.
5. **Run it once.** Go to Actions → "Find skips in new episodes" → Run workflow. It processes the 3 newest episodes per run (about 5–10 minutes each). To backfill faster, add a variable `MAX_NEW`, for example `10`.
6. **Add it to your phone.** Open the Pages URL and use Share → Add to Home Screen.

## Tuning

- **What gets skipped:** the `SYSTEM` prompt in `scripts/process.py` decides this. By default it keeps the cold open and the final proverb, and skips the credits along with the host outro. Edit the prompt if you want to keep the credits. To redo an episode, delete its entry from `data/episodes.json` and rerun the workflow.
- **Accuracy:** set the variable `WHISPER_MODEL` to `small.en` for a better transcript, at the cost of a slower run.
- **Ads that differ from the analysed copy:** if your copy's length doesn't match, the player shifts every skip by the difference. It assumes the extra ads came at the start, and it shows a note when it does this.
- **Overriding a skip:** if you tap into a hatched part of the bar yourself, that part plays instead of being skipped.

## Notes

- `data/episodes.json` contains only titles, links and timestamps, never audio.
- On iPhone, web pages can't change the volume, so the sleep timer stops playback at the end instead of fading out.
- Episodes never autoplay the next one.
