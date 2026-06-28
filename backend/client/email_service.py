import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from auth import config


class EmailService:
    """Sends transactional emails (invitations, verification, password reset,
    contact form, etc.).

    Backend selection (in order):
      1. Resend HTTP API — preferred. Set `RESEND_API_KEY` env var. No SMTP
         auth headaches, no Microsoft 365 conditional access policies, no
         app-password rituals. Free tier covers 3,000/mo, 100/day.
      2. SMTP fallback — used only when `RESEND_API_KEY` is unset (mainly
         local dev with Mailhog/Mailcatcher). Production should use Resend.
    """

    def __init__(self):
        self.resend_api_key = config.RESEND_API_KEY
        self.smtp_host = config.SMTP_HOST
        self.smtp_port = config.SMTP_PORT
        self.smtp_user = config.SMTP_USER
        self.smtp_password = config.SMTP_PASSWORD
        self.from_email = config.FROM_EMAIL
        self.from_name = config.FROM_NAME
        self.brand = config.FROM_NAME  # product name used in email copy
        self.support_email = config.SUPPORT_EMAIL
        self.frontend_url = config.FRONTEND_URL

    # ── Backend dispatch ─────────────────────────────────────────────────

    def _send(
        self,
        to_email: str,
        subject: str,
        html: str,
        reply_to: Optional[str] = None,
    ) -> None:
        """Single send entry-point used by every email method below."""
        if self.resend_api_key:
            self._send_via_resend(to_email, subject, html, reply_to)
        else:
            self._send_via_smtp(to_email, subject, html, reply_to)

    def _send_via_resend(
        self,
        to_email: str,
        subject: str,
        html: str,
        reply_to: Optional[str] = None,
    ) -> None:
        """Resend HTTP API. Lazy-import so a missing dependency only breaks
        email sending, not server boot."""
        import resend

        resend.api_key = self.resend_api_key
        payload: dict = {
            "from": f"{self.from_name} <{self.from_email}>",
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        if reply_to:
            payload["reply_to"] = reply_to
        resend.Emails.send(payload)

    def _send_via_smtp(
        self,
        to_email: str,
        subject: str,
        html: str,
        reply_to: Optional[str] = None,
    ) -> None:
        """Legacy SMTP send — kept for local dev / fallback."""
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{self.from_name} <{self.from_email}>"
        message["To"] = to_email
        if reply_to:
            message["Reply-To"] = reply_to
        message.attach(MIMEText(html, "html"))

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(message)

    # ── Public methods ───────────────────────────────────────────────────

    def send_invitation_email(
        self, to_email: str, token: str, organization_name: str, invited_by_name: str
    ):
        """Send organization invitation email"""
        invitation_url = f"{self.frontend_url}/invite/accept?token={token}"

        html = f"""
        <html>
        <body>
            <h2>You've been invited to join {organization_name}</h2>
            <p>{invited_by_name} has invited you to collaborate on {self.brand}.</p>
            <p>
                <a href="{invitation_url}"
                   style="background-color: #4CAF50; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 4px; display: inline-block;">
                    Accept Invitation
                </a>
            </p>
            <p style="color: #666; font-size: 12px;">
                This invitation expires in 7 days.<br>
                If you didn't expect this invitation, you can safely ignore this email.
            </p>
        </body>
        </html>
        """

        try:
            self._send(
                to_email=to_email,
                subject=f"Invitation to join {organization_name}",
                html=html,
            )
        except Exception as e:
            print(f"Failed to send email: {e}")
            raise

    def send_verification_email(self, to_email: str, token: str, user_name: str = None):
        """Send email verification magic link"""
        verification_url = f"{self.frontend_url}/auth/verify-email?token={token}"

        greeting = f"Hi {user_name}," if user_name else "Hello,"

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #4CAF50;">Verify Your Email Address</h2>
                <p>{greeting}</p>
                <p>Welcome to {self.brand}! Please verify your email address to activate your account and start using all features.</p>
                <p style="margin: 30px 0;">
                    <a href="{verification_url}"
                       style="background-color: #4CAF50; color: white; padding: 14px 28px;
                              text-decoration: none; border-radius: 4px; display: inline-block;
                              font-weight: bold;">
                        Verify Email Address
                    </a>
                </p>
                <p style="color: #666; font-size: 14px;">
                    Or copy and paste this link into your browser:<br>
                    <a href="{verification_url}" style="color: #4CAF50; word-break: break-all;">
                        {verification_url}
                    </a>
                </p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                <p style="color: #999; font-size: 12px;">
                    This verification link expires in 24 hours.<br>
                    If you didn't create an account with {self.brand}, you can safely ignore this email.
                </p>
            </div>
        </body>
        </html>
        """

        try:
            self._send(
                to_email=to_email,
                subject=f"Verify your email - {self.brand}",
                html=html,
            )
        except Exception as e:
            print(f"Failed to send verification email: {e}")
            raise

    def send_password_reset_email(
        self, to_email: str, token: str, user_name: str = None
    ):
        """Send password reset email with secure token link"""
        reset_url = f"{self.frontend_url}/auth/reset-password?token={token}"

        greeting = f"Hi {user_name}," if user_name else "Hello,"

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #4CAF50;">Reset Your Password</h2>
                <p>{greeting}</p>
                <p>We received a request to reset your password for your {self.brand} account. Click the button below to create a new password.</p>
                <p style="margin: 30px 0;">
                    <a href="{reset_url}"
                       style="background-color: #4CAF50; color: white; padding: 14px 28px;
                              text-decoration: none; border-radius: 4px; display: inline-block;
                              font-weight: bold;">
                        Reset Password
                    </a>
                </p>
                <p style="color: #666; font-size: 14px;">
                    Or copy and paste this link into your browser:<br>
                    <a href="{reset_url}" style="color: #4CAF50; word-break: break-all;">
                        {reset_url}
                    </a>
                </p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                <p style="color: #999; font-size: 12px;">
                    This password reset link expires in 1 hour.<br>
                    If you didn't request a password reset, you can safely ignore this email. Your password will not be changed.
                </p>
            </div>
        </body>
        </html>
        """

        try:
            self._send(
                to_email=to_email,
                subject=f"Reset your password - {self.brand}",
                html=html,
            )
        except Exception as e:
            print(f"Failed to send password reset email: {e}")
            raise

    def send_contact_form_email(
        self,
        from_name: str,
        from_email: str,
        from_company: str,
        from_phone: str,
        message_text: str,
        to_email: str = None,
    ):
        """
        Send contact form submission to support inbox with Reply-To header.

        Args:
            from_name: User's name
            from_email: User's email (will be set as Reply-To)
            from_company: User's company
            from_phone: User's phone number
            message_text: User's message
            to_email: Where to send the email (defaults to the configured support inbox)
        """
        to_email = to_email or self.support_email
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9f9f9; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #2563eb; margin-bottom: 20px;">New Contact Form Submission</h2>

                <div style="background-color: white; padding: 20px; border-radius: 6px; margin-bottom: 15px;">
                    <p style="margin: 10px 0;"><strong style="color: #555;">Name:</strong> {from_name}</p>
                    <p style="margin: 10px 0;"><strong style="color: #555;">Email:</strong>
                        <a href="mailto:{from_email}" style="color: #2563eb;">{from_email}</a>
                    </p>
                    <p style="margin: 10px 0;"><strong style="color: #555;">Company:</strong> {from_company or 'Not provided'}</p>
                    <p style="margin: 10px 0;"><strong style="color: #555;">Phone:</strong> {from_phone or 'Not provided'}</p>
                </div>

                <div style="background-color: white; padding: 20px; border-radius: 6px;">
                    <p style="margin: 0 0 10px 0;"><strong style="color: #555;">Message:</strong></p>
                    <p style="margin: 0; white-space: pre-wrap; color: #333;">{message_text}</p>
                </div>

                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">

                <p style="color: #999; font-size: 12px; margin: 0;">
                    This email was sent from the {self.brand} contact form.<br>
                    Click "Reply" to respond directly to {from_name}.
                </p>
            </div>
        </body>
        </html>
        """

        try:
            self._send(
                to_email=to_email,
                subject=f"[Contact Form] New inquiry from {from_name}",
                html=html,
                reply_to=from_email,  # KEY: Reply goes to user!
            )
        except Exception as e:
            print(f"Failed to send contact form email: {e}")
            raise

    def send_contact_confirmation_email(
        self, to_email: str, to_name: str, original_message: str
    ):
        """
        Send confirmation email to user who submitted contact form.

        Args:
            to_email: User's email address
            to_name: User's name
            original_message: Copy of their original message
        """
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #2563eb;">We Received Your Message!</h2>
                <p>Hi {to_name},</p>
                <p>Thank you for reaching out to {self.brand}. We've received your message and will respond within 24 hours.</p>

                <div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid #2563eb; margin: 20px 0;">
                    <p style="margin: 0 0 10px 0;"><strong>Your message:</strong></p>
                    <p style="margin: 0; white-space: pre-wrap; color: #555;">{original_message}</p>
                </div>

                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">

                <p style="color: #999; font-size: 12px;">
                    Best regards,<br>
                    The {self.brand} Team<br>
                    <a href="mailto:{self.support_email}" style="color: #2563eb;">{self.support_email}</a>
                </p>
            </div>
        </body>
        </html>
        """

        try:
            self._send(
                to_email=to_email,
                subject=f"We received your message - {self.brand}",
                html=html,
            )
        except Exception as e:
            print(f"Failed to send confirmation email: {e}")
            raise
