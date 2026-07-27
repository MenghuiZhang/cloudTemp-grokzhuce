import os
import unittest
from unittest.mock import Mock, patch

from g.email_service import EmailService


class FakeResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


class EmailServiceRandomSubdomainTests(unittest.TestCase):
    def make_service(self, *, domain="example.com", enabled="1", admin_key=""):
        session = Mock()
        session.post.return_value = FakeResponse(
            data={
                "address": "test@abc12345.example.com",
                "jwt": "address-jwt",
            }
        )
        env = {
            "WORKER_DOMAIN": "mail.example.workers.dev",
            "FREEMAIL_TOKEN": "site-password",
            "FREEMAIL_ADMIN_KEY": admin_key,
            "FREEMAIL_DOMAIN": domain,
            "FREEMAIL_RANDOM_SUBDOMAIN": enabled,
            "FREEMAIL_API_STYLE": "cf_temp",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("g.email_service.load_dotenv"),
            patch.object(EmailService, "_build_session", return_value=session),
        ):
            service = EmailService()
        return service, session

    def test_create_email_sends_random_subdomain_flag(self):
        service, session = self.make_service()

        jwt, email = service.create_email()

        self.assertEqual(jwt, "address-jwt")
        self.assertEqual(email, "test@abc12345.example.com")
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["domain"], "example.com")
        self.assertIs(payload["enableRandomSubdomain"], True)

    def test_create_email_does_not_send_flag_when_disabled(self):
        service, session = self.make_service(enabled="0")

        service.create_email()

        payload = session.post.call_args.kwargs["json"]
        self.assertNotIn("enableRandomSubdomain", payload)

    def test_auto_domain_uses_worker_random_subdomain_domain(self):
        service, session = self.make_service(domain="auto")

        with patch.object(
            EmailService,
            "fetch_mail_domains",
            return_value={"random_subdomain_domains": ["base.example"]},
        ):
            service.create_email()

        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["domain"], "base.example")
        self.assertIs(payload["enableRandomSubdomain"], True)

    def test_random_subdomain_does_not_fall_back_to_legacy_api(self):
        service, session = self.make_service()
        service._api_style = "auto"
        session.post.return_value = FakeResponse(
            status_code=400,
            text="Invalid random subdomain domain",
        )

        jwt, email = service.create_email()

        self.assertIsNone(jwt)
        self.assertIsNone(email)
        session.get.assert_not_called()

    def test_admin_key_uses_admin_create_endpoint_and_header(self):
        service, session = self.make_service(admin_key="admin-password")

        service.create_email()

        call = session.post.call_args
        self.assertEqual(
            call.args[0],
            "https://mail.example.workers.dev/admin/new_address",
        )
        self.assertEqual(
            call.kwargs["headers"]["x-admin-auth"],
            "admin-password",
        )
        self.assertEqual(
            call.kwargs["headers"]["x-custom-auth"],
            "site-password",
        )

    def test_fetch_domains_exposes_random_subdomain_domains(self):
        session = Mock()
        session.get.return_value = FakeResponse(
            data={
                "domains": ["one.example"],
                "defaultDomains": ["one.example"],
                "randomSubdomainDomains": ["random.example"],
            }
        )
        with (
            patch("g.email_service.load_dotenv"),
            patch.object(EmailService, "_build_session", return_value=session),
        ):
            result = EmailService.fetch_mail_domains(
                worker_domain="mail.example.workers.dev",
                token="site-password",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["random_subdomain_domains"],
            ["random.example"],
        )
        self.assertIn("random.example", result["domains"])


if __name__ == "__main__":
    unittest.main()
