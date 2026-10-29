"""Record classified delivery failures, lead handling, and bounce auto-pause."""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from sqlalchemy import delete as sql_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_settings import get_setting
from app.bounce_classify import Classification, classify_delivery_failure, is_delay_ndr
from app.models import Campaign, CampaignLead, DeliveryFailure, Inbox, Lead, QueueSlot
from app.time import utcnow

log = logging.getLogger("quickly.delivery_failures")

BOUNCE_AUTOPAUSE_ENABLED_KEY = "bounce_autopause_enabled"
BOUNCE_AUTOPAUSE_THRESHOLD_PCT_KEY = "bounce_autopause_threshold_pct"
BOUNCE_AUTOPAUSE_MIN_ATTEMPTS_KEY = "bounce_autopause_min_attempts"
BOUNCE_WARN_THRESHOLD_PCT_KEY = "bounce_warn_threshold_pct"

DEFAULT_AUTOPAUSE_ENABLED = True
DEFAULT_AUTOPAUSE_THRESHOLD_PCT = 5.0
DEFAULT_AUTOPAUSE_MIN_ATTEMPTS = 200
DEFAULT_WARN_THRESHOLD_PCT = 2.0

HEALTH_WINDOW_DAYS = 14
MAX_TRANSIENT_ATTEMPTS = 8


def _domain(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1]


def _dedupe_key(
    *,
    source: str,
    campaign_id: int | None,
    lead_id: int | None,
    inbox_id: int | None,
    sequence_index: int | None,
    kind: str,
    failure_class: str,
    to_email: str,
    window_minutes: int = 60,
) -> str:
    """Stable key so send-time + NDR for the same failure do not double-count."""
    bucket = int(utcnow().timestamp() // (window_minutes * 60))
    raw = "|".join(
        str(x)
        for x in (
            source,
            campaign_id,
            lead_id,
            inbox_id,
            sequence_index,
            kind,
            failure_class,
            (to_email or "").lower(),
            bucket,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


async def get_bounce_autopause_settings(db: AsyncSession) -> dict:
    enabled_raw = await get_setting(db, BOUNCE_AUTOPAUSE_ENABLED_KEY)
    thr_raw = await get_setting(db, BOUNCE_AUTOPAUSE_THRESHOLD_PCT_KEY)
    min_raw = await get_setting(db, BOUNCE_AUTOPAUSE_MIN_ATTEMPTS_KEY)
    warn_raw = await get_setting(db, BOUNCE_WARN_THRESHOLD_PCT_KEY)
    enabled = DEFAULT_AUTOPAUSE_ENABLED if enabled_raw is None else enabled_raw.lower() in ("true", "1", "yes")
    try:
        threshold = float(thr_raw) if thr_raw is not None else DEFAULT_AUTOPAUSE_THRESHOLD_PCT
    except (TypeError, ValueError):
        threshold = DEFAULT_AUTOPAUSE_THRESHOLD_PCT
    try:
        min_attempts = int(min_raw) if min_raw is not None else DEFAULT_AUTOPAUSE_MIN_ATTEMPTS
    except (TypeError, ValueError):
        min_attempts = DEFAULT_AUTOPAUSE_MIN_ATTEMPTS
    try:
        warn = float(warn_raw) if warn_raw is not None else DEFAULT_WARN_THRESHOLD_PCT
    except (TypeError, ValueError):
        warn = DEFAULT_WARN_THRESHOLD_PCT
    return {
        "enabled": enabled,
        "threshold_pct": threshold,
        "min_attempts": max(1, min_attempts),
        "warn_threshold_pct": warn,
    }


async def handle_ndr_bounce(
    db: AsyncSession,
    *,
    bounced_addr: str,
    inbox_id: int,
    subject: str,
    body: str,
    fire_webhook_event,
) -> bool:
    """Classify and record an NDR. Returns True if a failure was recorded / lead updated.

    Delay DSNs are ignored. Attribute to the most recent EmailLog for the lead when possible.
    """
    from app.models import EmailLog

    if is_delay_ndr(subject or "", body or ""):
        log.info("Ignoring delay NDR for %s (inbox_id=%s)", bounced_addr, inbox_id)
        return False

    lead = (
        await db.execute(select(Lead).where(func.lower(Lead.email) == bounced_addr.lower()))
    ).scalar_one_or_none()
    if lead is None:
        return False

    # Prefer most recent successful send for this lead from this inbox
    log_row = (
        await db.execute(
            select(EmailLog)
            .where(EmailLog.lead_id == lead.id, EmailLog.inbox_id == inbox_id)
            .order_by(EmailLog.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if log_row is None:
        log_row = (
            await db.execute(
                select(EmailLog)
                .where(EmailLog.lead_id == lead.id)
                .order_by(EmailLog.sent_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    campaign_id = log_row.campaign_id if log_row else None
    sequence_index = log_row.sequence_index if log_row else None
    reason = (body or subject or "NDR")[:500]

    inbox = (
        await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    ).scalar_one_or_none()

    clf = await record_delivery_failure(
        db,
        campaign_id=campaign_id,
        lead_id=lead.id,
        inbox_id=inbox_id,
        sequence_index=sequence_index,
        to_email=bounced_addr,
        error_type="bounce",
        error_message=reason,
        source="ndr",
        fire_webhook_event=fire_webhook_event,
        subject=subject,
        lead=lead,
        inbox=inbox,
    )
    return clf is not None


async def mark_lead_bounced_global(
    db: AsyncSession,
    *,
    lead: Lead,
    campaign_id: int | None,
    inbox_id: int | None,
    reason: str,
    fire_webhook_event,
) -> list[CampaignLead]:
    """Set enrollment_status=bounced for all active enrollments of this email; clear slots."""
    enrollments = (
        await db.execute(select(CampaignLead).where(CampaignLead.lead_id == lead.id))
    ).scalars().all()
    updated: list[CampaignLead] = []
    for cl in enrollments:
        if getattr(cl, "enrollment_status", None) in ("bounced", "unsubscribed"):
            continue
        prev = getattr(cl, "enrollment_status", None) or "active"
        cl.enrollment_status = "bounced"
        await db.execute(sql_delete(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id))
        updated.append(cl)
        await fire_webhook_event(
            db,
            "email.bounced",
            {
                "lead_id": lead.id,
                "lead_email": lead.email,
                "campaign_id": cl.campaign_id,
                "inbox_id": inbox_id,
                "error_message": reason,
                "timestamp": utcnow().isoformat() + "Z",
            },
        )
        await fire_webhook_event(
            db,
            "lead.status_changed",
            {
                "lead_id": lead.id,
                "lead_email": lead.email,
                "campaign_id": cl.campaign_id,
                "old_enrollment_status": prev,
                "new_enrollment_status": "bounced",
                "reason": reason,
                "timestamp": utcnow().isoformat() + "Z",
            },
        )
    return updated


async def pause_inbox_auth(
    db: AsyncSession,
    *,
    inbox: Inbox,
    error_type: str,
    message: str,
    fire_webhook_event,
    event_data: dict | None = None,
) -> bool:
    """Idempotent auth pause: only pause + webhook when currently unpaused."""
    res = await db.execute(
        update(Inbox)
        .where(Inbox.id == inbox.id, Inbox.paused.is_(False))
        .values(paused=True)
        .returning(Inbox.id)
    )
    newly = res.scalar_one_or_none() is not None
    inbox.paused = True
    if newly:
        log.warning(
            "Pausing inbox %s after %s — sending stops until credentials are fixed and resumed",
            inbox.email,
            error_type,
        )
        data = event_data or {
            "inbox_id": inbox.id,
            "inbox_email": inbox.email,
            "provider": inbox.provider,
            "error_type": error_type,
            "error_message": (message or "")[:500],
            "timestamp": utcnow().isoformat() + "Z",
        }
        await fire_webhook_event(db, "token_expired", data)
    return newly


async def apply_transient_backoff(slot: QueueSlot) -> bool:
    """Bump attempt_count / next_attempt_at. Returns False if cap exceeded (drop slot)."""
    attempts = int(getattr(slot, "attempt_count", 0) or 0) + 1
    slot.attempt_count = attempts
    if attempts > MAX_TRANSIENT_ATTEMPTS:
        return False
    # Exponential: 2, 4, 8, ... minutes capped at 60
    delay_min = min(60, 2 ** min(attempts, 6))
    slot.next_attempt_at = utcnow() + timedelta(minutes=delay_min)
    return True


async def record_delivery_failure(
    db: AsyncSession,
    *,
    campaign_id: int | None,
    lead_id: int | None,
    inbox_id: int | None,
    sequence_index: int | None,
    to_email: str,
    error_type: str,
    error_message: str,
    source: str,
    fire_webhook_event,
    smtp_stage: str | None = None,
    subject: str | None = None,
    http_status: int | None = None,
    classification: Classification | None = None,
    simulate_send: bool = False,
    skip_autopause: bool = False,
    lead: Lead | None = None,
    inbox: Inbox | None = None,
) -> Classification | None:
    """Persist a classified failure and apply lead / pause side effects.

    Returns the classification, or None if ignored (delay NDR / simulate / transient-only).
    """
    if simulate_send:
        return None

    if subject is not None and is_delay_ndr(subject, error_message or ""):
        log.info("Ignoring delay NDR for %s", to_email)
        return None

    clf = classification or classify_delivery_failure(
        error_type=error_type,
        message=error_message or "",
        source=source,
        smtp_stage=smtp_stage,
        subject=subject,
        http_status=http_status,
    )

    if clf.is_transient and clf.failure_class == "transient":
        # Do not persist transient toward bounce rates
        return clf

    if clf.failure_class in ("recipient_soft",) and clf.is_transient:
        return clf

    key = _dedupe_key(
        source=source if source != "ndr" else "any",
        campaign_id=campaign_id,
        lead_id=lead_id,
        inbox_id=inbox_id,
        sequence_index=sequence_index,
        kind=clf.kind,
        failure_class=clf.failure_class,
        to_email=to_email,
    )
    # Cross-source dedupe: send-time + NDR share bucket when lead+campaign known
    if lead_id and campaign_id is not None:
        key = _dedupe_key(
            source="any",
            campaign_id=campaign_id,
            lead_id=lead_id,
            inbox_id=inbox_id,
            sequence_index=sequence_index,
            kind=clf.kind,
            failure_class=clf.failure_class,
            to_email=to_email,
        )

    existing = (
        await db.execute(select(DeliveryFailure.id).where(DeliveryFailure.dedupe_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        log.debug("Skipping duplicate DeliveryFailure dedupe_key=%s", key)
        # Still apply lead bounce if needed (first path may have been NDR without lead mark race)
        if clf.mark_lead_bounced and lead is not None:
            await mark_lead_bounced_global(
                db,
                lead=lead,
                campaign_id=campaign_id,
                inbox_id=inbox_id,
                reason=(error_message or "")[:500],
                fire_webhook_event=fire_webhook_event,
            )
        return clf

    row = DeliveryFailure(
        campaign_id=campaign_id,
        lead_id=lead_id,
        inbox_id=inbox_id,
        sequence_index=sequence_index,
        kind=clf.kind,
        failure_class=clf.failure_class,
        source=source,
        error_type=(error_type or "")[:64],
        error_message=(error_message or "")[:2000],
        to_email=(to_email or "")[:255],
        recipient_domain=_domain(to_email)[:255],
        dedupe_key=key,
        created_at=utcnow(),
    )
    db.add(row)
    await db.flush()

    if clf.mark_lead_bounced:
        if lead is None and lead_id:
            lead = (
                await db.execute(select(Lead).where(Lead.id == lead_id))
            ).scalar_one_or_none()
        if lead is not None:
            await mark_lead_bounced_global(
                db,
                lead=lead,
                campaign_id=campaign_id,
                inbox_id=inbox_id,
                reason=(error_message or "")[:500],
                fire_webhook_event=fire_webhook_event,
            )

    if clf.pause_inbox_auth and inbox is not None:
        await pause_inbox_auth(
            db,
            inbox=inbox,
            error_type=error_type or "auth_failed",
            message=error_message or "",
            fire_webhook_event=fire_webhook_event,
            event_data={
                "inbox_id": inbox.id,
                "inbox_email": inbox.email,
                "provider": inbox.provider or "gmail",
                "error_type": error_type or "auth_failed",
                "error": (error_message or "")[:500],
                "error_message": (error_message or "")[:500],
                "timestamp": utcnow().isoformat() + "Z",
            },
        )

    if not skip_autopause and clf.kind in ("recipient", "sender"):
        await maybe_autopause(db, campaign_id=campaign_id, inbox_id=inbox_id, fire_webhook_event=fire_webhook_event)

    return clf


async def _count_attempts_campaign(db: AsyncSession, campaign_id: int, since) -> tuple[int, int, int]:
    """Returns (sent, recipient_failures, sender_failures) since watermark."""
    from app.models import EmailLog

    sent_q = select(func.count()).select_from(EmailLog).where(EmailLog.campaign_id == campaign_id)
    if since is not None:
        sent_q = sent_q.where(EmailLog.sent_at >= since)
    sent = (await db.execute(sent_q)).scalar() or 0

    fail_base = select(DeliveryFailure.kind, func.count()).where(
        DeliveryFailure.campaign_id == campaign_id,
        DeliveryFailure.kind.in_(("recipient", "sender")),
    )
    if since is not None:
        fail_base = fail_base.where(DeliveryFailure.created_at >= since)
    fail_base = fail_base.group_by(DeliveryFailure.kind)
    rows = (await db.execute(fail_base)).all()
    recipient = sender = 0
    for kind, cnt in rows:
        if kind == "recipient":
            recipient = int(cnt)
        elif kind == "sender":
            sender = int(cnt)
    return int(sent), recipient, sender


async def _count_attempts_inbox(db: AsyncSession, inbox_id: int, since) -> tuple[int, int, int]:
    from app.models import EmailLog

    sent_q = select(func.count()).select_from(EmailLog).where(EmailLog.inbox_id == inbox_id)
    if since is not None:
        sent_q = sent_q.where(EmailLog.sent_at >= since)
    sent = (await db.execute(sent_q)).scalar() or 0

    # Prefer distinct recipient domains for sender failures
    sender_q = select(func.count(func.distinct(DeliveryFailure.recipient_domain))).where(
        DeliveryFailure.inbox_id == inbox_id,
        DeliveryFailure.kind == "sender",
        DeliveryFailure.recipient_domain != "",
    )
    recip_q = select(func.count()).select_from(DeliveryFailure).where(
        DeliveryFailure.inbox_id == inbox_id,
        DeliveryFailure.kind == "recipient",
    )
    if since is not None:
        sender_q = sender_q.where(DeliveryFailure.created_at >= since)
        recip_q = recip_q.where(DeliveryFailure.created_at >= since)
    sender = (await db.execute(sender_q)).scalar() or 0
    # Fallback: if domains empty, count rows
    if sender == 0:
        sender_rows = select(func.count()).select_from(DeliveryFailure).where(
            DeliveryFailure.inbox_id == inbox_id,
            DeliveryFailure.kind == "sender",
        )
        if since is not None:
            sender_rows = sender_rows.where(DeliveryFailure.created_at >= since)
        sender = (await db.execute(sender_rows)).scalar() or 0
    recipient = (await db.execute(recip_q)).scalar() or 0
    return int(sent), int(recipient), int(sender)


async def maybe_autopause(
    db: AsyncSession,
    *,
    campaign_id: int | None,
    inbox_id: int | None,
    fire_webhook_event,
) -> None:
    cfg = await get_bounce_autopause_settings(db)
    if not cfg["enabled"]:
        return
    thr = cfg["threshold_pct"]
    min_n = cfg["min_attempts"]

    if campaign_id is not None:
        camp = (
            await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        ).scalar_one_or_none()
        if camp is not None and not camp.paused:
            sent, recip, _sender = await _count_attempts_campaign(
                db, campaign_id, camp.bounce_pause_watermark
            )
            attempts = sent + recip + _sender
            rate = (100.0 * recip / attempts) if attempts else 0.0
            if attempts >= min_n and rate >= thr:
                res = await db.execute(
                    update(Campaign)
                    .where(Campaign.id == campaign_id, Campaign.paused.is_(False))
                    .values(paused=True)
                    .returning(Campaign.id)
                )
                if res.scalar_one_or_none() is not None:
                    camp.paused = True
                    log.warning(
                        "Auto-pausing campaign %s: recipient bounce rate %.2f%% (n=%s)",
                        campaign_id,
                        rate,
                        attempts,
                    )
                    await fire_webhook_event(
                        db,
                        "campaign.bounce_autopause",
                        {
                            "campaign_id": campaign_id,
                            "recipient_bounce_rate": round(rate, 2),
                            "send_attempts": attempts,
                            "recipient_bounces": recip,
                            "threshold_pct": thr,
                            "timestamp": utcnow().isoformat() + "Z",
                        },
                    )

    if inbox_id is not None:
        inbox = (
            await db.execute(select(Inbox).where(Inbox.id == inbox_id))
        ).scalar_one_or_none()
        if inbox is not None and not inbox.paused:
            sent, _recip, sender = await _count_attempts_inbox(
                db, inbox_id, inbox.bounce_pause_watermark
            )
            attempts = sent + _recip + sender
            rate = (100.0 * sender / attempts) if attempts else 0.0
            if attempts >= min_n and rate >= thr:
                res = await db.execute(
                    update(Inbox)
                    .where(Inbox.id == inbox_id, Inbox.paused.is_(False))
                    .values(paused=True)
                    .returning(Inbox.id)
                )
                if res.scalar_one_or_none() is not None:
                    inbox.paused = True
                    log.warning(
                        "Auto-pausing inbox %s: sender bounce rate %.2f%% (n=%s)",
                        inbox.email,
                        rate,
                        attempts,
                    )
                    await fire_webhook_event(
                        db,
                        "inbox.bounce_autopause",
                        {
                            "inbox_id": inbox_id,
                            "inbox_email": inbox.email,
                            "sender_bounce_rate": round(rate, 2),
                            "send_attempts": attempts,
                            "sender_bounces": sender,
                            "threshold_pct": thr,
                            "timestamp": utcnow().isoformat() + "Z",
                        },
                    )


async def campaign_bounce_stats(db: AsyncSession, campaign_ids: list[int]) -> dict[int, dict]:
    """Aggregate bounce metrics for campaign list."""
    from app.models import EmailLog

    if not campaign_ids:
        return {}
    sent_rows = (
        await db.execute(
            select(EmailLog.campaign_id, func.count())
            .where(EmailLog.campaign_id.in_(campaign_ids))
            .group_by(EmailLog.campaign_id)
        )
    ).all()
    sent_map = {int(cid): int(c) for cid, c in sent_rows}

    fail_rows = (
        await db.execute(
            select(DeliveryFailure.campaign_id, DeliveryFailure.kind, func.count())
            .where(
                DeliveryFailure.campaign_id.in_(campaign_ids),
                DeliveryFailure.kind.in_(("recipient", "sender")),
            )
            .group_by(DeliveryFailure.campaign_id, DeliveryFailure.kind)
        )
    ).all()
    out: dict[int, dict] = {
        cid: {
            "recipient_bounces": 0,
            "sender_bounces": 0,
            "send_attempts": sent_map.get(cid, 0),
            "recipient_bounce_rate": 0.0,
            "sender_bounce_rate": 0.0,
        }
        for cid in campaign_ids
    }
    for cid, kind, cnt in fail_rows:
        if cid is None:
            continue
        d = out.setdefault(
            int(cid),
            {
                "recipient_bounces": 0,
                "sender_bounces": 0,
                "send_attempts": sent_map.get(int(cid), 0),
                "recipient_bounce_rate": 0.0,
                "sender_bounce_rate": 0.0,
            },
        )
        if kind == "recipient":
            d["recipient_bounces"] = int(cnt)
        elif kind == "sender":
            d["sender_bounces"] = int(cnt)
    for cid, d in out.items():
        attempts = d["send_attempts"] + d["recipient_bounces"] + d["sender_bounces"]
        d["send_attempts"] = attempts
        if attempts:
            d["recipient_bounce_rate"] = round(100.0 * d["recipient_bounces"] / attempts, 2)
            d["sender_bounce_rate"] = round(100.0 * d["sender_bounces"] / attempts, 2)
    return out


async def inbox_health_14d(db: AsyncSession, inbox_ids: list[int]) -> dict[int, dict]:
    from app.models import EmailLog

    if not inbox_ids:
        return {}
    since = utcnow() - timedelta(days=HEALTH_WINDOW_DAYS)
    sent_rows = (
        await db.execute(
            select(EmailLog.inbox_id, func.count())
            .where(EmailLog.inbox_id.in_(inbox_ids), EmailLog.sent_at >= since)
            .group_by(EmailLog.inbox_id)
        )
    ).all()
    sent_map = {int(iid): int(c) for iid, c in sent_rows if iid is not None}

    fail_rows = (
        await db.execute(
            select(DeliveryFailure.inbox_id, DeliveryFailure.kind, func.count())
            .where(
                DeliveryFailure.inbox_id.in_(inbox_ids),
                DeliveryFailure.kind.in_(("recipient", "sender")),
                DeliveryFailure.created_at >= since,
            )
            .group_by(DeliveryFailure.inbox_id, DeliveryFailure.kind)
        )
    ).all()
    out: dict[int, dict] = {}
    for iid in inbox_ids:
        out[iid] = {
            "health_window_days": HEALTH_WINDOW_DAYS,
            "sent_14d": sent_map.get(iid, 0),
            "recipient_bounces_14d": 0,
            "sender_bounces_14d": 0,
            "bounce_rate_14d": 0.0,
            "sender_bounce_rate_14d": 0.0,
        }
    for iid, kind, cnt in fail_rows:
        if iid is None:
            continue
        d = out.setdefault(
            int(iid),
            {
                "health_window_days": HEALTH_WINDOW_DAYS,
                "sent_14d": sent_map.get(int(iid), 0),
                "recipient_bounces_14d": 0,
                "sender_bounces_14d": 0,
                "bounce_rate_14d": 0.0,
                "sender_bounce_rate_14d": 0.0,
            },
        )
        if kind == "recipient":
            d["recipient_bounces_14d"] = int(cnt)
        elif kind == "sender":
            d["sender_bounces_14d"] = int(cnt)
    for iid, d in out.items():
        attempts = d["sent_14d"] + d["recipient_bounces_14d"] + d["sender_bounces_14d"]
        if attempts:
            d["bounce_rate_14d"] = round(100.0 * d["recipient_bounces_14d"] / attempts, 2)
            d["sender_bounce_rate_14d"] = round(100.0 * d["sender_bounces_14d"] / attempts, 2)
    return out
