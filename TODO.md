# TODO

Priority items to finish. Check this file at the start of every session, work through the open items, and tick each one (`- [x]`) with the date it was completed. Items here come before anything in a backlog.

## Open

- [x] **Verify the Notion push (Event Grid → notion-automation `track_health`).** Done 2026-10-10: simulated a new-day race (re-saved 10/5 blobs together) → one Health row + one card; backfilled 10/5–10/9 the same way. `health-sleep` merged to `main` here and in `notion-automation`.

- [ ] **Before any sleep/readiness backfill:** those writes now flow into Notion (one Health row + one habit card per day). Make sure `--metrics` excludes `sleep`/`readiness` unless you want Notion history filled in. The HRV/RHR backfill below is unaffected.

- [ ] **Confirm the webhooks write sleep + readiness on their own.** These were deployed on 2026-10-10, so the morning of 2026-10-11 is the first real test. After the ring syncs, run `uv run python scripts/check_health.py --days 2`. 2026-10-11 should show HRV + RHR + Sleep + Readiness. If Sleep or Readiness is missing, check the Function logs (TROUBLESHOOTING.md → "How to Check Function Logs").

- [ ] **Fix the HRV/RHR history.** Before the nap filter, evening dozes overwrote the night's data on **25 days**, and **58 nights** never got an HRV/RHR file. To fix it, run this yourself (it writes about 1,300 blobs, which Claude's permission check blocks):
  ```
  uv run python scripts/backfill.py --start 2025-01-01 --end 2026-10-10 --metrics hrv,rhr
  ```
  A local comparison on 2026-10-10 showed no other existing HRV/RHR file would change.

- [ ] **Decide what to do with 2 doze-only HRV/RHR files.** `2025-05-15` and `2026-09-26` only have a doze, no overnight sleep, so the backfill won't touch them. Delete them or keep them.

- [ ] **Consider rotating the Oura client secret and refresh token.** Before `.funcignore` was added on 2026-10-10, deploys likely uploaded `.env` to the Function App. Storage is private and the latest deploy replaced the package, so the risk is low. To rotate: create a new client secret in the Oura developer portal, then run `scripts/oauth_bootstrap.py`.

## Done

- [x] 2026-10-10: Added sleep times and the readiness score, plus the nap filter. Deployed, registered the readiness webhooks, backfilled sleep and readiness from 2025-01-01, and merged to `main`.
- [x] 2026-10-10: Fixed `fill_missing.py`. It asks Oura for `day..day+1`, skips naps, and fills all four files (HRV, RHR, sleep, readiness), uploading only what's missing. Verified on 2026-10-06 (doze + night → used the night) and on 2025-05-15 (doze only → nothing written).
- [x] 2026-10-10: Fixed the "Oura API connectivity" check in `check_health.py` (asks for `today..tomorrow`). It now sees last night's session. The health check's fix commands also now point to `fill_missing.py` for every missing file type.
