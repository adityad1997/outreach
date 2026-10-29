"""Classify delivery failures as recipient vs sender (and related classes).

First-match-wins rules. Do not trust coarse SendFailure.error_type alone —
historically ``bounce`` meant Gmail 400, Graph 400, and SMTP DATA 5xx.

Ambiguous / unknown permanent → recipient_hard (product choice 2B). If bounce
rates spike after ship, switch unknown_permanent to record-only / promote-on-repeat (2A).
See docs/INSTALL.md deliverability note.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Enhanced status: 5.7.708, 4.2.0, etc.
_ENHANCED_RE = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")
_BASIC_5XX_RE = re.compile(r"\b(5\d{2})\b")
_BASIC_4XX_RE = re.compile(r"\b(4\d{2})\b")

_POLICY_RE = re.compile(
    r"spamhaus|blocklist|blacklist|\bRBL\b|reputation|dmarc|\bspf\b|\bdkim\b|"
    r"unauthenticated|not authenticated|unsolicited|poor sender|SURBL|URIBL|\bDBL\b|"
    r"client host [\w\s]*blocked|relay access denied|sender.*rejected|"
    r"policy rejection|rejected by policy",
    re.IGNORECASE,
)
_RECIPIENT_HARD_PHRASE_RE = re.compile(
    r"no such user|user unknown|does not exist|invalid recipient|"
    r"mailbox (not found|unavailable)|recipient not found|account.*disabled|"
    r"unknown user|address rejected|invalid address|Invalid To header|"
    r"Recipient address required",
    re.IGNORECASE,
)
_MAILBOX_FULL_RE = re.compile(r"mailbox full|over quota|5\.2\.2|insufficient storage", re.IGNORECASE)
_SIZE_RE = re.compile(r"message too (large|big)|size limit|5\.3\.4|5\.2\.3", re.IGNORECASE)
_DELAY_SUBJECT_RE = re.compile(r"\(delay\)|delivery status notification \(delay\)", re.IGNORECASE)
_AUTH_PHRASE_RE = re.compile(
    r"authentication (required|failed)|credentials|invalid login|535|530 |"
    r"insufficientPermissions|ErrorSendAsDenied|ErrorAccessDenied|"
    r"Mail service not enabled|failedPrecondition",
    re.IGNORECASE,
)
_QUOTA_PHRASE_RE = re.compile(
    r"rateLimitExceeded|userRateLimitExceeded|dailyLimitExceeded|quotaExceeded|"
    r"ApplicationThrottled|ErrorSubmissionQuotaExceeded|daily user sending quota|"
    r"sending quota",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Classification:
    """Result of classify_delivery_failure."""

    failure_class: str
    # recipient | sender | other — what DeliveryFailure.kind stores / rates use
    kind: str
    # Mark lead bounced (lead-global) and emit email.bounced
    mark_lead_bounced: bool
    # Immediate inbox pause (auth)
    pause_inbox_auth: bool
    # Treat like transient: keep slot, no DeliveryFailure counting toward bounce rates
    is_transient: bool


def extract_enhanced_status(text: str) -> tuple[int, int, int] | None:
    if not text:
        return None
    m = _ENHANCED_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def extract_basic_code(text: str) -> int | None:
    if not text:
        return None
    m4 = _BASIC_4XX_RE.search(text)
    m5 = _BASIC_5XX_RE.search(text)
    # Prefer enhanced-driven logic elsewhere; basic codes for stage heuristics
    if m4 and (not m5 or text.find(m4.group(1)) < text.find(m5.group(1))):
        return int(m4.group(1))
    if m5:
        return int(m5.group(1))
    return None


def classify_delivery_failure(
    *,
    error_type: str = "",
    message: str = "",
    source: str = "smtp",
    smtp_stage: str | None = None,
    subject: str | None = None,
    http_status: int | None = None,
) -> Classification:
    """Classify a failure. First matching rule wins."""
    text = (message or "") + " " + (subject or "")
    et = (error_type or "").strip().lower()
    stage = (smtp_stage or "").strip().lower()
    enh = extract_enhanced_status(text)
    basic = extract_basic_code(text)

    # 1. Transient
    if subject and _DELAY_SUBJECT_RE.search(subject):
        return Classification("transient", "other", False, False, True)
    if "action: delayed" in text.lower() or "action:delayed" in text.lower().replace(" ", ""):
        return Classification("transient", "other", False, False, True)
    if enh and enh[0] == 4:
        return Classification("transient", "other", False, False, True)
    # HTTP status codes in error messages (e.g. "Gmail … (400)") must not be
    # treated as SMTP 4xx greylist. Prefer explicit http_status when provided.
    if http_status is None and basic and 400 <= basic < 500:
        return Classification("transient", "other", False, False, True)
    if http_status == 429:
        return Classification("transient", "other", False, False, True)
    if http_status is not None and 500 <= http_status < 600:
        return Classification("transient", "other", False, False, True)

    # 2. Sender auth
    if et in ("auth_failed", "permission_denied"):
        if _QUOTA_PHRASE_RE.search(text):
            return Classification("sender_quota", "sender", False, False, True)
        return Classification("sender_auth", "sender", False, True, False)
    if http_status == 401 or (http_status == 403 and _AUTH_PHRASE_RE.search(text) and not _QUOTA_PHRASE_RE.search(text)):
        return Classification("sender_auth", "sender", False, True, False)
    if basic in (530, 534, 535, 538) or (enh and enh[0] == 5 and enh[1] == 7 and enh[2] in (8, 9)):
        return Classification("sender_auth", "sender", False, True, False)
    if _AUTH_PHRASE_RE.search(text) and not _RECIPIENT_HARD_PHRASE_RE.search(text):
        if "failedprecondition" in text.lower() or "mail service not enabled" in text.lower():
            return Classification("sender_auth", "sender", False, True, False)

    # 3. Sender quota
    if _QUOTA_PHRASE_RE.search(text) or (http_status == 403 and "ratelimit" in text.lower()):
        return Classification("sender_quota", "sender", False, False, True)

    # 4. Sender policy (before recipient 5.1.1)
    if _POLICY_RE.search(text):
        return Classification("sender_policy", "sender", False, False, False)

    # 5. Recipient hard via enhanced codes
    if enh and enh[0] == 5 and enh[1] == 1 and enh[2] in (1, 2, 3, 6, 10):
        return Classification("recipient_hard", "recipient", True, False, False)
    if enh and enh[0] == 5 and enh[1] == 2 and enh[2] == 1:
        return Classification("recipient_hard", "recipient", True, False, False)

    # 6. Content / size
    if _SIZE_RE.search(text) or (enh and enh[0] == 5 and enh[1] == 6):
        return Classification("content", "other", False, False, False)
    if enh and enh[0] == 5 and enh[1] == 2 and enh[2] == 3:
        return Classification("content", "other", False, False, False)

    # 7. Remaining 5.7.x → sender policy (DATA stage never recipient unless phrase)
    if enh and enh[0] == 5 and enh[1] == 7:
        if enh[2] in (133, 134):
            return Classification("recipient_policy", "other", False, False, False)
        return Classification("sender_policy", "sender", False, False, False)

    # SMTP DATA 5xx without clear recipient phrase → sender/content, not recipient
    if stage == "data" and ((basic and 500 <= basic < 600) or (enh and enh[0] == 5)):
        if _RECIPIENT_HARD_PHRASE_RE.search(text):
            return Classification("recipient_hard", "recipient", True, False, False)
        return Classification("sender_policy", "sender", False, False, False)

    # 8. Recipient soft
    if _MAILBOX_FULL_RE.search(text) or (enh and enh[0] == 5 and enh[1] == 2 and enh[2] in (0, 2)):
        return Classification("recipient_soft", "other", False, False, True)

    # Phrase-based recipient hard (550 without enhanced)
    if _RECIPIENT_HARD_PHRASE_RE.search(text):
        return Classification("recipient_hard", "recipient", True, False, False)

    # Map legacy error_type when message is unhelpful
    if et == "invalid_recipient":
        return Classification("recipient_hard", "recipient", True, False, False)
    if et == "bounce":
        # Ambiguous legacy bounce — 2B treats as recipient
        return Classification("unknown_permanent", "recipient", True, False, False)

    # HTTP 404 stale thread → not recipient
    if http_status == 404 or "erroritemnotfound" in text.lower():
        return Classification("sender_state", "other", False, False, True)

    # 9. Ambiguous → recipient hard (2B)
    return Classification("unknown_permanent", "recipient", True, False, False)


def is_delay_ndr(subject: str, body: str = "") -> bool:
    text = f"{subject or ''} {body or ''}"
    if _DELAY_SUBJECT_RE.search(subject or ""):
        return True
    low = text.lower()
    if "action: delayed" in low or "action:delayed" in low.replace(" ", ""):
        return True
    enh = extract_enhanced_status(text)
    if enh and enh[0] == 4:
        return True
    return False
