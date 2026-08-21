# -----------------------------------------------------------------------
# renew_subscriptions.py — Timer-triggered Azure Function
# -----------------------------------------------------------------------
# Oura webhook subscriptions expire if not renewed. If they expire,
# delivery stops *silently* — you won't get an error, your pipeline
# just stops receiving data. This is the exact kind of silent failure
# we moved away from GitHub Actions to avoid.
#
# This function runs once a day (at noon UTC) and renews all active
# subscriptions. It's a simple safety net:
#   1. List all current webhook subscriptions
#   2. Renew each one
#   3. Log the result (check Application Insights if something fails)
#
# The cron schedule "0 0 12 * * *" means: second 0, minute 0, hour 12,
# every day, every month, every day-of-week. Azure Functions uses
# NCrontab format (6 fields: sec min hour day month dow).
# -----------------------------------------------------------------------

import logging

import azure.functions as func

from core.oura_client import list_webhook_subscriptions, renew_webhook_subscription

logger = logging.getLogger(__name__)

# Blueprint — registered in function_app.py alongside the webhook receiver
bp = func.Blueprint()


# Timer trigger: runs daily at noon UTC.
# run_on_startup=False prevents this from firing every time you deploy
# or restart the Function App (which would be noisy and unnecessary).
@bp.timer_trigger(schedule="0 0 12 * * *", arg_name="timer", run_on_startup=False)
def renew_subscriptions(timer: func.TimerRequest) -> None:
    """Daily renewal of Oura webhook subscriptions."""

    # past_due is True if the Function App was stopped/offline when this
    # timer was supposed to fire. We still renew — better late than never.
    if timer.past_due:
        logger.warning("Timer is past due — running renewal anyway")

    try:
        subs = list_webhook_subscriptions()
    except Exception:
        logger.exception("Failed to list webhook subscriptions")
        return

    if not subs:
        logger.warning("No webhook subscriptions found — did they expire?")
        return

    for sub in subs:
        sub_id = sub.get("id")
        data_type = sub.get("data_type", "?")
        event_type = sub.get("event_type", "?")

        try:
            renew_webhook_subscription(sub_id)
            logger.info(f"Renewed subscription {sub_id} ({data_type}/{event_type})")
        except Exception:
            logger.exception(
                f"Failed to renew subscription {sub_id} ({data_type}/{event_type})"
            )
