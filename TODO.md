# TODO

Priority items to finish. Check this file at the start of every session, work through the open items, and tick each one (`- [x]`) with the date it was completed. Items here come before anything in a backlog.

## Open

- [x] **Verify the Notion push (Event Grid → notion-automation `track_health`).** Done 2026-10-10: simulated a new-day race (re-saved 10/5 blobs together) → one Health row + one card; backfilled 10/5–10/9 the same way. `health-sleep` merged to `main` here and in `notion-automation`.

- [ ] **Before any sleep/readiness backfill:** those writes now flow into Notion (one Health row + one habit card per day). Make sure `--metrics` excludes `sleep`/`readiness` unless you want Notion history filled in. The HRV/RHR backfill below is unaffected.

- [ ] **Confirm readiness now arrives with the sleep event.** Sleep worked on its own from 2026-10-12. Readiness didn't, because Oura never sent a `daily_readiness` webhook. Fixed and deployed 2026-10-15 (see Done). The morning of **2026-10-16** is the first real test. After the ring syncs, `readiness/2026/10/2026-10-16_readiness.json` should exist, and the logs should show `Wrote readiness/…` right after `Wrote sleep/…`. Also run `uv run python scripts/check_health.py --days 2`.

- [ ] **Fix the HRV/RHR history.** Before the nap filter, evening dozes overwrote the night's data on **25 days**, and **58 nights** never got an HRV/RHR file. To fix it, run this yourself (it writes about 1,300 blobs, which Claude's permission check blocks):
  ```
  uv run python scripts/backfill.py --start 2025-01-01 --end 2026-10-10 --metrics hrv,rhr
  ```
  A local comparison on 2026-10-10 showed no other existing HRV/RHR file would change.

- [ ] **Decide what to do with 2 doze-only HRV/RHR files.** `2025-05-15` and `2026-09-26` only have a doze, no overnight sleep, so the backfill won't touch them. Delete them or keep them.

- [ ] **Consider rotating the Oura client secret and refresh token.** Before `.funcignore` was added on 2026-10-10, deploys likely uploaded `.env` to the Function App. Storage is private and the latest deploy replaced the package, so the risk is low. To rotate: create a new client secret in the Oura developer portal, then run `scripts/oauth_bootstrap.py`.

## Done

- [x] 2026-10-15: **Readiness now rides on the sleep event.** App Insights showed every webhook POST from 10/9 to 10/15 was a sleep event, and none was ever `daily_readiness`, although both readiness subscriptions renew daily. So the readiness blobs had stopped after the 10/10 backfill. `webhook_receiver` now fetches that day's readiness (`fetch_readiness_for_day`) after writing an overnight sleep's files. Best effort: a failure is logged and never fails the sleep event. Added 4 tests (87 pass) and live-checked the lookup against Oura. Deployed, then ran `fill_missing.py` for 10/12–10/15 (scores 63/53/69/71). 10/11 had no data because the ring wasn't worn. `track_health` wrote all four to Notion.

- [x] 2026-10-10: Added sleep times and the readiness score, plus the nap filter. Deployed, registered the readiness webhooks, backfilled sleep and readiness from 2025-01-01, and merged to `main`.
- [x] 2026-10-10: Fixed `fill_missing.py`. It asks Oura for `day..day+1`, skips naps, and fills all four files (HRV, RHR, sleep, readiness), uploading only what's missing. Verified on 2026-10-06 (doze + night → used the night) and on 2025-05-15 (doze only → nothing written).
- [x] 2026-10-10: Fixed the "Oura API connectivity" check in `check_health.py` (asks for `today..tomorrow`). It now sees last night's session. The health check's fix commands also now point to `fill_missing.py` for every missing file type.
