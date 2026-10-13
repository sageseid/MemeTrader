"""Email sending. The first service that is configured is used:

1. Resend (resend.com), when RESEND_API_KEY is set. An email API for apps: it works from
   GitHub's servers and doesn't filter by content. Free: 100 emails a day. Without a domain of
   your own it sends from onboarding@resend.dev, and only to your Resend account's address.
2. Formspree (formspree.io), when FORMSPREE_FORM is set. Works from GitHub, but its spam
   filter silently drops emails that contain prices and percentages (it still answers "ok"),
   so alerts and scorecards go missing. Fine for the plain test email only.
3. FormSubmit (formsubmit.co), otherwise. Works from a home connection but blocks GitHub's
   servers, and needs a one-time "Activate Form" click per address.
"""

import html

import requests

RESEND = "https://api.resend.com/emails"
RESEND_FROM = "Meme Signal Bot <onboarding@resend.dev>"
FORMSPREE = "https://formspree.io/f/{form}"
FORMSUBMIT = "https://formsubmit.co/ajax/{to}"


class EmailError(Exception):
    pass


def provider(cfg):
    if cfg.get("resend_key"):
        return "Resend"
    return "Formspree" if cfg.get("formspree_form") else "FormSubmit"


def _html(fields):
    rows = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #ddd;font-weight:600;vertical-align:top'>"
        f"{html.escape(str(k))}</td><td style='padding:6px 12px;border-bottom:1px solid #ddd'>"
        f"{html.escape(str(v))}</td></tr>" for k, v in fields.items())
    return f"<table style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>{rows}</table>"


def send(subject, fields, cfg, dry_run=False):
    """fields: ordered {label: value}, rendered as a two-column table.
    cfg: {"to", "resend_key", "formspree_form", "formsubmit_referer"}."""
    name = provider(cfg)
    if dry_run:
        print(f"\n--- EMAIL (dry run, via {name}) to {cfg['to']}\nSubject: {subject}")
        for k, v in fields.items():
            print(f"  {k}: {v}")
        return
    body = {k: str(v) for k, v in fields.items()}
    if name == "Resend":
        url = RESEND
        payload = {"from": RESEND_FROM, "to": [cfg["to"]], "subject": subject, "html": _html(body),
                   "text": "\n".join(f"{k}: {v}" for k, v in body.items())}
        headers = {"Authorization": f"Bearer {cfg['resend_key']}"}
    elif name == "Formspree":
        url = FORMSPREE.format(form=cfg["formspree_form"])
        payload = {"_subject": subject, **body}
        headers = {"Accept": "application/json"}
    else:
        url = FORMSUBMIT.format(to=cfg["to"])
        payload = {"_subject": subject, "_template": "table", "_captcha": "false", **body}
        referer = cfg["formsubmit_referer"]
        headers = {"Accept": "application/json", "Referer": referer,
                   "Origin": "/".join(referer.split("/", 3)[:3])}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise EmailError(f"{name} request failed: {e}") from e
    try:
        data = r.json()
    except ValueError:
        raise EmailError(f"{name} answered HTTP {r.status_code} with a web page instead of JSON "
                         f"(usually a block on this network): {r.text[:150]!r}") from None
    if name == "Resend":
        ok = bool(data.get("id"))
    elif name == "Formspree":
        ok = data.get("ok")
    else:
        ok = str(data.get("success")).lower() == "true"
    if r.status_code >= 400 or not ok:
        raise EmailError(f"{name} did not send (HTTP {r.status_code}): "
                         f"{data.get('error') or data.get('message') or data}")
