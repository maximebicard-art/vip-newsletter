#!/usr/bin/env python3
"""
VIP Newsletter — Email Senders
v4.0: Extracted from monolithic v3.36.
"""
import os
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from .config import EMAIL_CONFIG, RECIPIENT_EMAIL


class EmailSender:
    def __init__(self, config=None):
        self.config = config or EMAIL_CONFIG
        self.provider = self.config.get("provider", "brevo").lower()

    def send(self, html_content, plain_text, subject):
        if self.provider == "brevo":
            return self._send_brevo(html_content, plain_text, subject)
        elif self.provider == "sendpulse":
            return self._send_sendpulse(html_content, plain_text, subject)
        elif self.provider == "mailjet":
            return self._send_mailjet(html_content, plain_text, subject)
        elif self.provider == "sendgrid":
            return self._send_sendgrid(html_content, plain_text, subject)
        elif self.provider == "smtp":
            return self._send_smtp(html_content, plain_text, subject)
        else:
            print(f"❌ Unknown email provider: {self.provider}")
            return False

    def _send_brevo(self, html, text, subject):
        api_key = self.config.get("brevo_api_key", "")
        if not api_key:
            print("❌ BREVO_API_KEY not set")
            return False
        try:
            resp = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": api_key, "Content-Type": "application/json"},
                json={
                    "sender": {"name": self.config["from_name"], "email": self.config["from_email"]},
                    "to": [{"email": RECIPIENT_EMAIL}],
                    "subject": subject,
                    "htmlContent": html,
                    "textContent": text,
                },
                timeout=30,
            )
            if resp.status_code in (200, 201, 202):
                print(f"✅ Brevo: Email sent successfully")
                return True
            else:
                print(f"❌ Brevo error {resp.status_code}: {resp.text[:200]}")
                return False
        except Exception as e:
            print(f"❌ Brevo exception: {e}")
            return False

    def _send_sendpulse(self, html, text, subject):
        print("⚠️ SendPulse sender not yet implemented in v4.0")
        return False

    def _send_mailjet(self, html, text, subject):
        print("⚠️ Mailjet sender not yet implemented in v4.0")
        return False

    def _send_sendgrid(self, html, text, subject):
        print("⚠️ SendGrid sender not yet implemented in v4.0")
        return False

    def _send_smtp(self, html, text, subject):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.config['from_name']} <{self.config['from_email']}>"
            msg["To"] = RECIPIENT_EMAIL
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            server = smtplib.SMTP(self.config["smtp_server"], self.config["smtp_port"])
            server.starttls()
            server.login(self.config["smtp_username"], self.config["smtp_password"])
            server.sendmail(self.config["from_email"], [RECIPIENT_EMAIL], msg.as_string())
            server.quit()
            print(f"✅ SMTP: Email sent successfully")
            return True
        except Exception as e:
            print(f"❌ SMTP exception: {e}")
            return False
