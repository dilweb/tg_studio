from html import escape


def verification_email_html(verify_url: str, first_name: str) -> str:
    name = escape(first_name or "there")
    safe_url = escape(verify_url)
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:system-ui,-apple-system,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width:480px;background:#fff;border-radius:12px;
          box-shadow:0 1px 3px rgba(0,0,0,.08);">
          <tr><td style="padding:28px 24px 8px;font-size:20px;font-weight:600;color:#111;">Confirm your email</td></tr>
          <tr><td style="padding:8px 24px 20px;font-size:15px;line-height:1.5;color:#444;">
            Hi {name},<br><br>
            Please confirm your email address to finish setting up your account.
          </td></tr>
          <tr><td style="padding:0 24px 24px;">
            <a href="{safe_url}" style="display:inline-block;background:#2563eb;color:#fff !important;
              text-decoration:none;font-weight:600;font-size:15px;padding:12px 24px;border-radius:8px;">
              Verify email
            </a>
          </td></tr>
          <tr><td style="padding:0 24px 24px;font-size:13px;color:#888;word-break:break-all;">
            If the button does not work, copy this link:<br>
            <span style="color:#2563eb;">{safe_url}</span>
          </td></tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
