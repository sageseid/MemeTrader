"""Email through a form-to-email service: no SMTP, no password.

Formspree (formspree.io) is used when a form id is set (FORMSPREE_FORM env var or
`formspree_form` in settings.toml). It accepts requests from GitHub's servers;
its free plan allows 50 emails a month. Emails go to the Formspree account's address.

Otherwise FormSubmit (formsubmit.co) is used. It works from a home connection but
blocks GitHub's servers. The first send to a new address triggers a one-time
"Activate Form" email from FormSubmit; nothing is delivered until you click it.
"""

import requests

FORMSPREE = "https://formspree.io/f/{form}"
FORMSUBMIT = "https://formsubmit.co/ajax/{to}"


class EmailError(Exception):
    pass


def send(subject, fields, cfg, dry_run=False):
    """fields: ordered {label: value}; both services render them as a table.
    cfg: {"to", "formspree_form", "formsubmit_referer"}."""
    if dry_run:
        via = "Formspree" if cfg["formspree_form"] else "FormSubmit"
        print(f"\n--- EMAIL (dry run, via {via}) to {cfg['to']}\nSubject: {subject}")
        for k, v in fields.items():
            print(f"  {k}: {v}")
        return
    body = {k: str(v) for k, v in fields.items()}
    if cfg["formspree_form"]:
        name = "Formspree"
        url = FORMSPREE.format(form=cfg["formspree_form"])
        payload = {"_subject": subject, **body}
        headers = {"Accept": "application/json"}
    else:
        name = "FormSubmit"
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
    ok = data.get("ok") if name == "Formspree" else str(data.get("success")).lower() == "true"
    if r.status_code >= 400 or not ok:
        raise EmailError(f"{name} did not send (HTTP {r.status_code}): "
                         f"{data.get('error') or data.get('message') or data}")
