"""Table-driven bounce classifier + delivery-failure side effects."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.bounce_classify import classify_delivery_failure, is_delay_ndr
from app.models import DeliveryFailure, EmailLog
from app.sender import SendFailure
from tests.conftest import (
    make_campaign,
    make_campaign_inbox,
    make_campaign_lead,
    make_inbox,
    make_lead,
    make_queue_slot,
    make_sequence,
)


@pytest.mark.parametrize(
    "kwargs,expected_class,expected_kind,mark_lead,pause_auth,transient",
    [
        (
            {"message": "450 4.2.0 Greylisted", "smtp_stage": "rcpt"},
            "transient",
            "other",
            False,
            False,
            True,
        ),
        (
            {"message": "554 5.7.1 Service unavailable; Client host blocked by Spamhaus"},
            "sender_policy",
            "sender",
            False,
            False,
            False,
        ),
        (
            {"message": "550 5.1.1 User unknown"},
            "recipient_hard",
            "recipient",
            True,
            False,
            False,
        ),
        (
            {"message": "550 5.7.708 Access denied"},
            "sender_policy",
            "sender",
            False,
            False,
            False,
        ),
        (
            {"message": "552 5.2.2 Mailbox full"},
            "recipient_soft",
            "other",
            False,
            False,
            True,
        ),
        (
            {"message": "SMTP rejected the message (554): content rejected", "smtp_stage": "data"},
            "sender_policy",
            "sender",
            False,
            False,
            False,
        ),
        (
            {
                "error_type": "bounce",
                "message": "Gmail rejected the message (400): {}",
                "http_status": 400,
            },
            "unknown_permanent",
            "recipient",
            True,
            False,
            False,
        ),
        (
            {"error_type": "auth_failed", "message": "535 Authentication failed"},
            "sender_auth",
            "sender",
            False,
            True,
            False,
        ),
        (
            {
                "message": "User rate limit exceeded: rateLimitExceeded",
                "http_status": 403,
                "error_type": "permission_denied",
            },
            "sender_quota",
            "sender",
            False,
            False,
            True,
        ),
    ],
)
def test_classifier_table(kwargs, expected_class, expected_kind, mark_lead, pause_auth, transient):
    clf = classify_delivery_failure(**kwargs)
    assert clf.failure_class == expected_class
    assert clf.kind == expected_kind
    assert clf.mark_lead_bounced is mark_lead
    assert clf.pause_inbox_auth is pause_auth
    assert clf.is_transient is transient


def test_delay_ndr_detected():
    assert is_delay_ndr("Delivery Status Notification (Delay)", "Action: delayed")
    assert not is_delay_ndr("Undeliverable: Hello", "550 5.1.1 User unknown")


@pytest.mark.asyncio
async def test_recipient_bounce_marks_lead_global(session, monkeypatch):
    import app.jobs as jobs_mod
    from app.models import SmtpAccount
    from tests.test_send_auth_failure import _SessionCtx

    inbox = await make_inbox(session, email="send@example.com", provider="smtp")
    session.add(
        SmtpAccount(
            inbox_id=inbox.id,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
            smtp_use_tls=True,
        )
    )
    campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4, 5, 6],
                                   sending_hours_start="00:00", sending_hours_end="23:59")
    campaign2 = await make_campaign(session, name="Other")
    await make_sequence(session, campaign.id)
    lead = await make_lead(session, email="bad@lead.example")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    cl2 = await make_campaign_lead(session, campaign2.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    slot = await make_queue_slot(
        session, cl.id, inbox.id, scheduled_date=datetime.utcnow() - timedelta(minutes=1)
    )
    await session.flush()

    events = []

    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr(
        "app.jobs.send_email",
        lambda **kwargs: SendFailure(
            error_type="invalid_recipient",
            message="550 5.1.1 User unknown",
            smtp_stage="rcpt",
        ),
    )
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))

    await jobs_mod.send_slot_job(slot.id)

    await session.refresh(cl)
    await session.refresh(cl2)
    assert cl.enrollment_status == "bounced"
    assert cl2.enrollment_status == "bounced"
    assert any(e[0] == "email.bounced" for e in events)
    df_count = (
        await session.execute(select(func.count(DeliveryFailure.id)).where(DeliveryFailure.kind == "recipient"))
    ).scalar()
    assert df_count == 1


@pytest.mark.asyncio
async def test_sender_policy_does_not_mark_lead(session, monkeypatch):
    import app.jobs as jobs_mod
    from app.models import SmtpAccount
    from tests.test_send_auth_failure import _SessionCtx

    inbox = await make_inbox(session, email="send2@example.com", provider="smtp")
    session.add(
        SmtpAccount(
            inbox_id=inbox.id,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
            smtp_use_tls=True,
        )
    )
    campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4, 5, 6],
                                   sending_hours_start="00:00", sending_hours_end="23:59")
    await make_sequence(session, campaign.id)
    lead = await make_lead(session, email="ok@lead.example")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    slot = await make_queue_slot(
        session, cl.id, inbox.id, scheduled_date=datetime.utcnow() - timedelta(minutes=1)
    )
    await session.flush()

    async def fake_webhook(db, event, data):
        return None

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr(
        "app.jobs.send_email",
        lambda **kwargs: SendFailure(
            error_type="bounce",
            message="554 5.7.1 blocked by spamhaus",
            smtp_stage="data",
        ),
    )
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))

    await jobs_mod.send_slot_job(slot.id)

    await session.refresh(cl)
    assert cl.enrollment_status != "bounced"
    df = (
        await session.execute(select(DeliveryFailure).where(DeliveryFailure.kind == "sender"))
    ).scalar_one()
    assert df.failure_class == "sender_policy"


@pytest.mark.asyncio
async def test_simulate_send_writes_no_delivery_failure(session, monkeypatch):
    import app.jobs as jobs_mod
    from app.settings_manager import settings
    from tests.test_send_auth_failure import _SessionCtx

    from app.models import GmailAccount

    inbox = await make_inbox(session, email="sim@example.com", provider="gmail")
    session.add(
        GmailAccount(
            inbox_id=inbox.id,
            google_email=inbox.email,
            access_token="tok",
            refresh_token="ref",
        )
    )
    campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4, 5, 6],
                                   sending_hours_start="00:00", sending_hours_end="23:59")
    await make_sequence(session, campaign.id)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    slot = await make_queue_slot(
        session, cl.id, inbox.id, scheduled_date=datetime.utcnow() - timedelta(minutes=1)
    )
    await session.flush()

    monkeypatch.setattr(settings, "test_mode", True)
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    await jobs_mod.send_slot_job(slot.id)

    assert (
        await session.execute(select(func.count(DeliveryFailure.id)))
    ).scalar() == 0
    assert (
        await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    ).scalar() == 1


@pytest.mark.asyncio
async def test_ndr_delay_ignored(session):
    from app.delivery_failures import handle_ndr_bounce

    lead = await make_lead(session, email="delay@lead.example")
    inbox = await make_inbox(session, email="ndr-box@example.com")
    campaign = await make_campaign(session)
    await make_campaign_lead(session, campaign.id, lead.id)

    async def fake_webhook(db, event, data):
        raise AssertionError("should not fire")

    ok = await handle_ndr_bounce(
        session,
        bounced_addr=lead.email,
        inbox_id=inbox.id,
        subject="Delivery Status Notification (Delay)",
        body="Action: delayed\nDiagnostic-Code: smtp; 450",
        fire_webhook_event=fake_webhook,
    )
    assert ok is False
    assert (
        await session.execute(select(func.count(DeliveryFailure.id)))
    ).scalar() == 0


@pytest.mark.asyncio
async def test_autopause_campaign_on_recipient_rate(session):
    from app.app_settings import put_setting
    from app.delivery_failures import (
        BOUNCE_AUTOPAUSE_ENABLED_KEY,
        BOUNCE_AUTOPAUSE_MIN_ATTEMPTS_KEY,
        BOUNCE_AUTOPAUSE_THRESHOLD_PCT_KEY,
        maybe_autopause,
    )

    inbox = await make_inbox(session, email="ap@example.com")
    campaign = await make_campaign(session)
    await put_setting(session, BOUNCE_AUTOPAUSE_ENABLED_KEY, "true")
    await put_setting(session, BOUNCE_AUTOPAUSE_THRESHOLD_PCT_KEY, "5")
    await put_setting(session, BOUNCE_AUTOPAUSE_MIN_ATTEMPTS_KEY, "10")
    await session.flush()

    # 10 sent + 1 recipient failure = ~9% < need more failures
    for i in range(10):
        session.add(
            EmailLog(
                lead_id=(await make_lead(session, email=f"s{i}@x.com")).id,
                campaign_id=campaign.id,
                inbox_id=inbox.id,
                sequence_index=0,
                subject="x",
                message_id=f"<m{i}@t>",
            )
        )
    for i in range(1):
        session.add(
            DeliveryFailure(
                campaign_id=campaign.id,
                lead_id=None,
                inbox_id=inbox.id,
                kind="recipient",
                failure_class="recipient_hard",
                source="smtp",
                error_type="bounce",
                error_message="550",
                to_email=f"b{i}@x.com",
                recipient_domain="x.com",
                dedupe_key=f"ap-recip-{i}",
            )
        )
    await session.flush()

    events = []

    async def fake_webhook(db, event, data):
        events.append(event)

    # 1/11 ≈ 9% but min_attempts=10 and threshold 5% — should pause
    await maybe_autopause(
        session, campaign_id=campaign.id, inbox_id=None, fire_webhook_event=fake_webhook
    )
    await session.refresh(campaign)
    assert campaign.paused is True
    assert "campaign.bounce_autopause" in events
