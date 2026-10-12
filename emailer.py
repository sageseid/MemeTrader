"""Email through FormSubmit (formsubmit.co): no SMTP, no password.

The first send to a new address triggers a one-time "Activate Form" email from
FormSubmit. Nothing is delivered until you click that link.
"""

import requests

ENDPOINT = "https://formsubmit.co/ajax/{to}"


class EmailError(Exception):
    pass


def send(subject, fields, to, referer, dry_run=False):
    """fields: ordered {label: value}; FormSubmit renders them as a two-column table."""
    if dry_run:
        print(f"\n--- EMAIL (dry run) to {to}\nSubject: {subject}")
        for k, v in fields.items():
            print(f"  {k}: {v}")
        return
    payload = {"_subject": subject, "_template": "table", "_captcha": "false"}
    payload.update({k: str(v) for k, v in fields.items()})
    try:
        r = requests.post(ENDPOINT.format(to=to), json=payload, timeout=30, headers={
            "Accept": "application/json",
            "Referer": referer,
            "Origin": referer.split("/", 3)[0] + "//" + referer.split("/", 3)[2],
        })
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise EmailError(f"FormSubmit request failed: {e}") from e
    if str(data.get("success")).lower() != "true":
        raise EmailError(f"FormSubmit did not send: {data.get('message') or data}")
