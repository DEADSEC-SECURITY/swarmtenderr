# Screenshots and screen tour

These assets show the published **0.7.0 synthetic demo**. Names, hashes, queue
states, history and rates are illustrative. They contain no private installation
data or real client connection. The source screenshots are unmodified browser
captures; the ranking image frames the inspector to keep its text readable.

- [Dashboard](overview.jpg): queue state, active transfers, completion totals,
  daily history and transfer history.
- [Ranking breakdown](ranking.jpg): base estimate, each ranking contribution,
  final weighted cost and evidence tier.
- [Settings](settings.jpg): aligned ranking/discovery sections, readable units
  and a ranking interval saved as six minutes.
- [55-second screen tour (MP4)](walkthrough.mp4): edited browser captures with
  burned-in captions, 1920 × 1080, H.264, no audio.
- [Animated README preview (GIF)](walkthrough.gif): the same 55-second tour at
  960 × 540, two frames per second, looping.
- [SubRip captions](walkthrough.srt).

The walkthrough shows an actual UI edit from five to six minutes and a successful
save. The local demo was then returned to five minutes. Demo mode prevents real
client mutations; its recommendations are synthetic.

## Transcript

| Time | What the tour shows |
| --- | --- |
| 0:00–0:05 | Swarmtenderr: understand and control your qBittorrent queue. |
| 0:05–0:09 | Daily completions, category totals and transfer history. |
| 0:09–0:13 | Switch to monthly history to compare longer periods. |
| 0:13–0:18 | See optimizer rank beside the current native queue position. |
| 0:18–0:23 | Inspect a torrent to understand the current recommendation. |
| 0:23–0:29 | Read each score contribution. Weighted cost is not a download ETA. |
| 0:29–0:35 | Missing metadata is not scored. Discovery uses a separate trial. |
| 0:35–0:39 | Tune ranking and discovery using hours, minutes and seconds. |
| 0:39–0:43 | Change the ranking interval from five minutes to six. |
| 0:43–0:49 | Save the setting in the UI. The demo cannot change a real client. |
| 0:49–0:55 | Try it with Docker: github.com/DEADSEC-SECURITY/swarmtenderr. |

Every chapter identifies the synthetic demo and release. The closing disclosure
says: “Mostly developed by AI under my supervision. I am a software developer;
I reviewed everything and tested it on my own system.”

## Reuse and refresh

The assets are part of the project's [MIT license](../../LICENSE). For project
announcements, include the synthetic-data and AI-development disclosures
alongside the tour. To refresh them, start the README's disposable demo, sign in as
the demo administrator, capture Overview, inspect ranking/metadata, and save a
duration change in Settings. Use synthetic data throughout and identify the
version captured. New captures should accompany a UI change before replacing
these files.
