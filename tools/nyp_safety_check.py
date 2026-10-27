"""
Plain-code safety checks (Step 3). No model, no network calls.

Edit the RULES list and the phrase lists below to tune behaviour. Each rule gives an
evidence code, a confidence adjustment, the risk level it implies on its own, a
reason shown to the agent ({detail} filled in) and a detail-free label for views.

The tool records only the evidence codes, risk and quarantine flag for the message in
the store, so nyp_save_analysis reads the evidence from code instead of trusting the
model to pass it on. Reasons (which can quote the email) are returned, never stored.
"""
import re
from typing import List, Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

import nyp_store as store

RULES = [
    {"code": "SENDER_DOMAIN_KNOWN",   "adjust": +10, "risk": "low",    "reason": "Sender domain {detail} is a known contact.",
     "label": "Sender domain is a known contact"},
    {"code": "LOOKALIKE_DOMAIN",      "adjust": -40, "risk": "high",   "reason": "Sender domain {detail}.",
     "label": "Sender domain imitates a known domain"},
    {"code": "REPLY_TO_MISMATCH",     "adjust": -15, "risk": "medium", "reason": "Replies would go to a different domain ({detail}).",
     "label": "Reply-To goes to a different domain"},
    {"code": "LINK_DOMAIN_MISMATCH",  "adjust": -10, "risk": "medium", "reason": "Links point to a different domain than the sender ({detail}).",
     "label": "Links point to a different domain than the sender"},
    {"code": "URGENT_LANGUAGE",       "adjust": -10, "risk": "medium", "reason": "Uses pressure wording ({detail}).",
     "label": "Uses urgent or pressure wording"},
    {"code": "BANK_DETAILS_CHANGED",  "adjust": -30, "risk": "high",   "reason": "Payment email says bank or account details have changed; needs human review.",
     "label": "Payment email with changed bank details"},
    {"code": "PROMPT_INJECTION_TEXT", "adjust": -30, "risk": "high",   "reason": "Contains instructions aimed at an AI assistant ({detail}).",
     "label": "Contains instructions aimed at an AI"},
    {"code": "AUTH_FAIL",             "adjust": -20, "risk": "medium", "reason": "Email authentication failed ({detail}).",
     "label": "SPF, DKIM or DMARC check failed"},
    {"code": "BULK_SENDER",           "adjust": 0,   "risk": "low",    "reason": "Newsletter or marketing email with a valid signature and an unsubscribe link.",
     "label": "Newsletter or marketing sender"},
    # Recorded by nyp_calendar_conflicts, not by check() below.
    {"code": "CALENDAR_CLASH",        "adjust": -10, "risk": "low",    "reason": "The proposed time clashes with an existing event.",
     "label": "Clashes with an existing calendar event"},
]
RULES_BY_CODE = {r["code"]: r for r in RULES}


def label(code: str) -> str:
    """Plain-English, detail-free description of an evidence code."""
    return RULES_BY_CODE[code]["label"] if code in RULES_BY_CODE else code


def confidence_adjustment(codes: List[str]) -> int:
    return sum(RULES_BY_CODE[c]["adjust"] for c in codes if c in RULES_BY_CODE)

# Domains you already trust, in addition to the known_contacts passed by the agent.
KNOWN_DOMAINS: List[str] = []

# Account-security pressure: always a warning, newsletter or not.
SECURITY_URGENT_PHRASES = [
    r"verify your account", r"account (will be )?(suspended|locked|closed|disabled)",
    r"confirm your (password|identity|details|login)", r"unusual sign-?in activity", r"reset your password now",
]
# Sales-style pressure: a warning in personal mail, normal in verified newsletters.
URGENT_PHRASES = SECURITY_URGENT_PHRASES + [
    r"\bimmediately\b", r"within 24 hours", r"\burgent(ly)?\b", r"act now", r"final notice",
]
# Link hosts used by mail services, social networks and CDNs. A link there is not a sign
# that the email pretends to be someone else.
TRUSTED_LINK_DOMAINS = {
    "list-manage.com", "mailchimp.com", "mcusercontent.com", "sendgrid.net", "mandrillapp.com", "mailgun.org",
    "hubspotlinks.com", "hubspot.com", "hs-sites.com", "exacttarget.com", "sfmc-content.com", "klaviyo.com",
    "klclick.com", "mailjet.com", "sparkpostmail.com", "amazonses.com", "awstrack.me", "cloudfront.net",
    "googleusercontent.com", "google.com", "gstatic.com", "youtube.com", "youtu.be", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "linkedin.com", "tiktok.com", "apple.com", "w3.org",
    "microsoft.com", "office.com",
}
DKIM_PASS_RE = re.compile(r"\bdkim\s*=\s*pass\b", re.I)
PAYMENT_WORDS = [r"\binvoice\b", r"\bpayment\b", r"\bremit", r"\bwire\b", r"\btransfer\b", r"\bbilling\b"]
BANK_CHANGE_PHRASES = [
    r"(new|updated|changed?|different)\s+(bank|banking|account|payment)\s+(details|information|info|number)",
    r"(bank|banking|account|payment)\s+(details|information|number)\s+(have|has)\s+(changed|been updated)",
    r"(changed|updated)\s+our\s+(bank|banking)",
]
INJECTION_PHRASES = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts|messages)",
    r"disregard (all |any )?(previous|prior|your) (instructions|rules)",
    r"you are (now )?an? (ai|assistant|language model)",
    r"(as an|dear) (ai|assistant)",
    r"forward (all|every|this|these) (mail|emails?|messages?)",
    r"system prompt", r"skip (the )?approval", r"do not (ask|tell) the user",
    r"reveal (your|the) (instructions|data|password)",
]
AUTH_FAIL_RE = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*(fail|softfail|permerror)\b", re.I)

# Second-level suffixes, so "mail.example.com.sg" reduces to "example.com.sg".
MULTI_PART_SUFFIXES = {"com.sg", "edu.sg", "gov.sg", "org.sg", "net.sg", "co.uk", "ac.uk",
                       "com.au", "co.jp", "com.my", "co.in", "co.nz"}

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def _domain_of(address: Optional[str]) -> str:
    """'Name <a@Mail.Example.com>' -> 'mail.example.com'."""
    if not address:
        return ""
    m = re.search(r"@([A-Za-z0-9.-]+)", address)
    return m.group(1).lower().strip(".") if m else address.lower().strip(" <>.")


def base_domain(domain: str) -> str:
    parts = domain.lower().strip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _first_match(patterns: List[str], text: str) -> Optional[str]:
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


def check(sender_address: str, reply_to_address: Optional[str], subject: str, body: str,
          link_domains: Optional[List[str]], auth_results: Optional[str],
          known_contacts: Optional[List[str]], bulk_mail: bool = False) -> dict:
    """Run every rule and return evidence codes, risk, confidence adjustment and reasons.
    bulk_mail = the email has a List-Unsubscribe header. Together with a passing DKIM signature it
    marks a verified newsletter, which gets the sales-style rules relaxed (never the fraud rules)."""
    hits = {}  # code -> detail
    text = f"{subject or ''}\n{body or ''}"
    verified_bulk = bool(bulk_mail) and bool(DKIM_PASS_RE.search(auth_results or "")) \
        and not AUTH_FAIL_RE.search(auth_results or "")

    sender = base_domain(_domain_of(sender_address))
    known = {base_domain(_domain_of(c)) for c in list(known_contacts or []) + KNOWN_DOMAINS if c}
    known.discard("")

    if sender and sender in known:
        hits["SENDER_DOMAIN_KNOWN"] = sender
    elif sender:
        for k in sorted(known):
            if _edit_distance(sender, k) <= 2:
                hits["LOOKALIKE_DOMAIN"] = f"{sender} looks like known domain {k}"
                break

    reply_to = base_domain(_domain_of(reply_to_address))
    if reply_to and sender and reply_to != sender and not verified_bulk:
        hits["REPLY_TO_MISMATCH"] = f"from {sender}, reply-to {reply_to}"

    link_bases = {base_domain(d) for d in (link_domains or []) if d}
    foreign = sorted(link_bases - {sender} - TRUSTED_LINK_DOMAINS)
    links_home = sender in link_bases
    if sender and foreign and not (verified_bulk and links_home):
        hits["LINK_DOMAIN_MISMATCH"] = ", ".join(foreign[:5])

    urgent = _first_match(SECURITY_URGENT_PHRASES if verified_bulk else URGENT_PHRASES, text)
    if urgent:
        hits["URGENT_LANGUAGE"] = f'"{urgent.lower()}"'

    if _first_match(PAYMENT_WORDS, text) and _first_match(BANK_CHANGE_PHRASES, text):
        hits["BANK_DETAILS_CHANGED"] = ""

    injection = _first_match(INJECTION_PHRASES, text)
    if injection:
        hits["PROMPT_INJECTION_TEXT"] = f'"{injection.lower()}"'

    auth_fails = sorted({f"{m.group(1).lower()}={m.group(2).lower()}" for m in AUTH_FAIL_RE.finditer(auth_results or "")})
    if auth_fails:
        hits["AUTH_FAIL"] = ", ".join(auth_fails)

    if verified_bulk:
        hits["BULK_SENDER"] = ""

    evidence, reasons, adjust, risk = [], [], 0, "low"
    for rule in RULES:
        if rule["code"] in hits:
            evidence.append(rule["code"])
            reasons.append(rule["reason"].format(detail=hits[rule["code"]]))
            adjust += rule["adjust"]
            if RISK_ORDER[rule["risk"]] > RISK_ORDER[risk]:
                risk = rule["risk"]
    # Several independent warning signs together are treated as high risk, except for a
    # verified newsletter, where they are common and harmless (fraud rules still apply above).
    if risk == "medium" and not verified_bulk \
            and sum(1 for r in RULES if r["code"] in hits and r["risk"] == "medium") >= 3:
        risk = "high"
        reasons.append("Several warning signs together, so this is treated as high risk.")

    return {"evidence": evidence, "risk": risk, "confidence_adjustment": adjust,
            "quarantine": risk == "high", "reasons": reasons}


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=store.DB_CREDENTIALS)
def nyp_safety_check(message_id: str, sender_address: str, reply_to_address: str, subject: str, body: str,
                     link_domains: List[str], auth_results: str, known_contacts: List[str],
                     bulk_mail: bool = False) -> dict:
    """
    Run plain-code safety rules on one email. Call this before nyp_save_analysis. Never opens links or calls the network.

    Args:
        message_id (str): Gmail message id.
        sender_address (str): From address.
        reply_to_address (str): Reply-To address, or empty.
        subject (str): Email subject.
        body (str): Plain-text body.
        link_domains (List[str]): Domains of links found in the body.
        auth_results (str): Authentication-Results header, or empty.
        known_contacts (List[str]): Addresses or domains the user already trusts.
        bulk_mail (bool): True if the email has a List-Unsubscribe header (newsletter); optional.
    Returns:
        dict: {message_id, evidence: [codes], risk: low|medium|high, confidence_adjustment: int, quarantine: bool, reasons: [str]}.
    """
    result = check(sender_address, reply_to_address, subject, body, link_domains, auth_results, known_contacts,
                   bulk_mail)
    store.record_safety(message_id, result["risk"], result["evidence"], result["quarantine"])
    store.log("nyp_safety_check", f"checked {message_id}",
              f"risk={result['risk']} evidence={','.join(result['evidence']) or 'none'}")
    return {"message_id": message_id, **result}
