import datetime
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

def build_full_html_email(price_changes, disappeared=[], new_competitors=[], no_changes=False):
    date_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    def build_rows(items, is_price=False, price_color_fn=None):
        if not items:
            return """<tr><td colspan='4' style='padding: 10px; text-align: center; color: gray;'>✅ No items in this category.</td></tr>"""
        rows = ""
        for idx, item in enumerate(items, 1):
            if is_price:
                color = price_color_fn(item['old_price'], item['new_price'])
                rows += f"<tr><td>{idx}</td><td><a href='{item['url']}' style='color: #1a73e8;'>View Item</a></td><td style='text-align:right;'>${item['old_price']:.2f}</td><td style='text-align:right;color:{color};'>${item['new_price']:.2f}</td></tr>"
            else:
                rows += f"<tr><td>{idx}</td><td colspan='3'><a href='{item}' style='color: #1a73e8;'>{item}</a></td></tr>"
        return rows

    def color_fn(old, new):
        return "green" if new < old else "red"

    if no_changes:
        content = "<p style='font-size: 16px; color: #333;'>✅ No changes detected in your tracked competitors.</p>"
    else:
        price_section = f"<h3 style='color:#333;margin-top:30px;'>💲 Price Changes</h3><table style='width:100%;border-collapse:collapse;margin-top:10px;'><thead><tr style='background-color:#f1f1f1;'><th style='padding:10px;'>#</th><th style='padding:10px;'>Product</th><th style='padding:10px;text-align:right;'>Old</th><th style='padding:10px;text-align:right;'>New</th></tr></thead><tbody>{build_rows(price_changes, is_price=True, price_color_fn=color_fn)}</tbody></table>" if price_changes else ""
        disappeared_section = f"<h3 style='color:#333;margin-top:30px;'>🚨 Disappeared Listings</h3><table style='width:100%;border-collapse:collapse;margin-top:10px;'><thead><tr style='background-color:#f1f1f1;'><th style='padding:10px;'>#</th><th style='padding:10px;'>Listing URL</th><th colspan='2'></th></tr></thead><tbody>{build_rows(disappeared)}</tbody></table>" if disappeared else ""
        new_section = f"<h3 style='color:#333;margin-top:30px;'>🆕 New Competitors</h3><table style='width:100%;border-collapse:collapse;margin-top:10px;'><thead><tr style='background-color:#f1f1f1;'><th style='padding:10px;'>#</th><th style='padding:10px;'>Listing URL</th><th colspan='2'></th></tr></thead><tbody>{build_rows(new_competitors)}</tbody></table>" if new_competitors else ""
        content = price_section + disappeared_section + new_section

    html = f"""
    <html><body style='font-family: Arial, sans-serif; background-color: #f9f9f9; padding: 20px;'>
      <div style='max-width: 600px; margin: auto; background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>

        <div style='text-align: center; margin-bottom: 10px;'>
          <img src='https://i.imgur.com/Ou0o6kI.png' alt='CompetiView Logo' style='max-height: 100px;'>
        </div>

        <h2 style='color: #1a73e8; border-bottom: 1px solid #eee; padding-bottom: 10px;'>📢 CompetiView Tracker Update</h2>

        {content}

        <p style='margin-top: 30px; font-size: 14px; color: #555;'>📅 Checked on: <strong>{date_str}</strong><br>🔄 Next check in 24 hours.</p>

        <p style='font-size: 14px; color: #888; border-top: 1px solid #eee; padding-top: 15px; text-align: center;'>CompetiView Tracker &mdash; You are receiving this because you subscribed to alerts.</p>

      </div>
    </body></html>
    """
    return html



def send_email(recipient, subject, plain_body, html_body=None):
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    sender_email = "siroguz02@gmail.com"
    sender_password = "ghnltqhpoyywunmu"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"CompetiView <{sender_email}>"
    msg["To"] = recipient

    msg.attach(MIMEText(plain_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient, msg.as_string())
        print(f"📧 Email sent to {recipient}")
    except Exception as e:
        print(f"❗ Failed to send email: {e}")

