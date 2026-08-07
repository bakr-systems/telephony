# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import frappe
from frappe.tests import IntegrationTestCase
from twilio.request_validator import RequestValidator
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from telephony.twilio.api import (
    twilio_incoming_call_handler,
    update_call_status_info,
    update_recording_info,
    voice,
)

AUTH_TOKEN = "test_auth_token"
BASE_URL = "https://test.example.com"
VOICE_URL = f"{BASE_URL}/api/method/telephony.twilio.api.voice"
INCOMING_URL = (
    f"{BASE_URL}/api/method/telephony.twilio.api.twilio_incoming_call_handler"
)
RECORDING_URL = f"{BASE_URL}/api/method/telephony.twilio.api.update_recording_info"
STATUS_URL = f"{BASE_URL}/api/method/telephony.twilio.api.update_call_status_info"


class TestTwilioWebhookSignature(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.validator = RequestValidator(AUTH_TOKEN)
        self._previous_request = getattr(frappe.local, "request", None)
        self.voice_form = {
            "AccountSid": "ACxxxxxxxx",
            "ApplicationSid": "APxxxxxxxx",
            "Caller": "client:test@example.com",
            "To": "+1234567890",
            "CallSid": "CAxxxxxxxx",
            "CallStatus": "ringing",
            "link_doctype": "",
            "link_docname": "",
        }
        self.incoming_form = {
            "From": "+1234567890",
            "To": "+1987654321",
            "CallSid": "CAxxxxxxxx",
            "CallStatus": "ringing",
        }
        self.recording_form = {
            "CallSid": "CAxxxxxxxx",
            "RecordingUrl": "https://example.com/recording.mp3",
        }
        self.status_form = {
            "ParentCallSid": "CAparent",
            "CallSid": "CAchild",
            "CallStatus": "completed",
            "CallDuration": "30",
            "From": "+1234567890",
            "To": "+1987654321",
        }

    def tearDown(self):
        frappe.local.request = self._previous_request
        super().tearDown()

    def _make_request(self, url, form_data, signature=""):
        parsed = urlsplit(url)
        builder = EnvironBuilder(
            method="POST",
            path=parsed.path,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
            query_string=parsed.query,
            data=form_data,
            headers={"X-Twilio-Signature": signature},
        )
        request = builder.get_request(Request)
        self.assertEqual(request.url, url)
        return request

    def _set_request(self, request):
        frappe.local.request = request

    def _settings_patch(self, enabled=1, auth_token=AUTH_TOKEN):
        settings = MagicMock()
        settings.enabled = enabled
        settings.account_sid = "ACxxxxxxxx"
        settings.twiml_sid = "APxxxxxxxx"
        settings.api_key = "SKxxxxxxxx"

        def _get_password(field, raise_exception=True):
            self.assertFalse(raise_exception)
            return {"auth_token": auth_token, "api_secret": "secret"}.get(field)

        settings.get_password = MagicMock(side_effect=_get_password)
        return patch("frappe.get_doc", return_value=settings)

    def _valid_signature(self, url, form_data):
        return self.validator.compute_signature(url, form_data)

    def test_voice_accepts_valid_signature(self):
        signature = self._valid_signature(VOICE_URL, self.voice_form)
        self._set_request(self._make_request(VOICE_URL, self.voice_form, signature))

        mock_twilio = MagicMock()
        mock_resp = MagicMock()
        mock_resp.to_xml.return_value = "<Response/>"
        mock_twilio.generate_twilio_dial_response.return_value = mock_resp

        with self._settings_patch():
            with patch("telephony.twilio.api.Twilio.connect", return_value=mock_twilio):
                with patch("frappe.db.get_value", return_value="+1111111111"):
                    with patch(
                        "telephony.twilio.api.create_call_log"
                    ) as mock_create_call_log:
                        response = voice(**self.voice_form)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/xml")
        mock_twilio.generate_twilio_dial_response.assert_called_once()
        mock_create_call_log.assert_called_once()

    def test_voice_denies_invalid_signature(self):
        signature = self._valid_signature(VOICE_URL, self.voice_form) + "tampered"
        self._set_request(self._make_request(VOICE_URL, self.voice_form, signature))

        with self._settings_patch():
            with patch("telephony.twilio.api.Twilio.connect") as mock_connect:
                with patch(
                    "telephony.twilio.api.create_call_log"
                ) as mock_create_call_log:
                    with self.assertRaises(frappe.PermissionError):
                        voice(**self.voice_form)

        mock_connect.assert_not_called()
        mock_create_call_log.assert_not_called()

    def test_voice_denies_missing_signature(self):
        self._set_request(self._make_request(VOICE_URL, self.voice_form, signature=""))

        with self._settings_patch():
            with self.assertRaises(frappe.PermissionError):
                voice(**self.voice_form)

    def test_voice_denies_wrong_url(self):
        wrong_url = f"{VOICE_URL}?extra=param"
        signature = self._valid_signature(VOICE_URL, self.voice_form)
        self._set_request(self._make_request(wrong_url, self.voice_form, signature))

        with self._settings_patch():
            with self.assertRaises(frappe.PermissionError):
                voice(**self.voice_form)

    def test_voice_denies_when_disabled(self):
        signature = self._valid_signature(VOICE_URL, self.voice_form)
        self._set_request(self._make_request(VOICE_URL, self.voice_form, signature))

        with self._settings_patch(enabled=0):
            with patch("telephony.twilio.api.Twilio.connect") as mock_connect:
                with self.assertRaises(frappe.PermissionError):
                    voice(**self.voice_form)

        mock_connect.assert_not_called()

    def test_voice_denies_when_auth_token_missing(self):
        signature = self._valid_signature(VOICE_URL, self.voice_form)
        self._set_request(self._make_request(VOICE_URL, self.voice_form, signature))

        with self._settings_patch(auth_token=None):
            with patch("telephony.twilio.api.Twilio.connect") as mock_connect:
                with patch(
                    "telephony.twilio.api.create_call_log"
                ) as mock_create_call_log:
                    with self.assertRaises(frappe.PermissionError):
                        voice(**self.voice_form)

        mock_connect.assert_not_called()
        mock_create_call_log.assert_not_called()

    def test_incoming_call_accepts_valid_signature(self):
        signature = self._valid_signature(INCOMING_URL, self.incoming_form)
        self._set_request(
            self._make_request(INCOMING_URL, self.incoming_form, signature)
        )

        mock_resp = MagicMock()
        mock_resp.to_xml.return_value = "<Response/>"

        with self._settings_patch():
            with patch("telephony.twilio.api.IncomingCall") as MockIncomingCall:
                mock_instance = MockIncomingCall.return_value
                mock_instance.process.return_value = mock_resp
                with patch(
                    "telephony.twilio.api.create_call_log"
                ) as mock_create_call_log:
                    response = twilio_incoming_call_handler(**self.incoming_form)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/xml")
        MockIncomingCall.assert_called_once_with(
            self.incoming_form["From"], self.incoming_form["To"]
        )
        mock_create_call_log.assert_called_once()

    def test_incoming_call_denies_invalid_signature(self):
        signature = self._valid_signature(INCOMING_URL, self.incoming_form) + "tampered"
        self._set_request(
            self._make_request(INCOMING_URL, self.incoming_form, signature)
        )

        with self._settings_patch():
            with patch("telephony.twilio.api.IncomingCall") as MockIncomingCall:
                with patch(
                    "telephony.twilio.api.create_call_log"
                ) as mock_create_call_log:
                    with self.assertRaises(frappe.PermissionError):
                        twilio_incoming_call_handler(**self.incoming_form)

        MockIncomingCall.assert_not_called()
        mock_create_call_log.assert_not_called()

    def test_incoming_call_denies_disabled_settings(self):
        signature = self._valid_signature(INCOMING_URL, self.incoming_form)
        self._set_request(
            self._make_request(INCOMING_URL, self.incoming_form, signature)
        )

        with self._settings_patch(enabled=0):
            with self.assertRaises(frappe.PermissionError):
                twilio_incoming_call_handler(**self.incoming_form)

    def test_update_recording_info_accepts_valid_signature(self):
        signature = self._valid_signature(RECORDING_URL, self.recording_form)
        self._set_request(
            self._make_request(RECORDING_URL, self.recording_form, signature)
        )

        with self._settings_patch():
            with patch("telephony.twilio.api.update_call_log") as mock_update_call_log:
                with patch("frappe.db.set_value") as mock_set_value:
                    update_recording_info(**self.recording_form)

        mock_update_call_log.assert_called_once_with(self.recording_form["CallSid"])
        mock_set_value.assert_called_once()

    def test_update_recording_info_denies_invalid_signature_before_side_effects(self):
        signature = (
            self._valid_signature(RECORDING_URL, self.recording_form) + "tampered"
        )
        self._set_request(
            self._make_request(RECORDING_URL, self.recording_form, signature)
        )

        with self._settings_patch():
            with patch("telephony.twilio.api.update_call_log") as mock_update_call_log:
                with patch("frappe.db.set_value") as mock_set_value:
                    with self.assertRaises(frappe.PermissionError):
                        update_recording_info(**self.recording_form)

        mock_update_call_log.assert_not_called()
        mock_set_value.assert_not_called()

    def test_update_call_status_info_accepts_valid_signature(self):
        signature = self._valid_signature(STATUS_URL, self.status_form)
        self._set_request(self._make_request(STATUS_URL, self.status_form, signature))

        mock_client = MagicMock()

        with self._settings_patch():
            with patch("telephony.twilio.api.update_call_log") as mock_update_call_log:
                with patch(
                    "telephony.twilio.api.Twilio.get_twilio_client",
                    return_value=mock_client,
                ):
                    update_call_status_info(**self.status_form)

        mock_update_call_log.assert_called_once_with(
            self.status_form["ParentCallSid"], status=self.status_form["CallStatus"]
        )
        mock_client.calls.assert_called_once_with(self.status_form["ParentCallSid"])
        (
            mock_client.calls.return_value.user_defined_messages.create.assert_called_once()
        )

    def test_update_call_status_info_denies_invalid_signature_before_side_effects(self):
        signature = self._valid_signature(STATUS_URL, self.status_form) + "tampered"
        self._set_request(self._make_request(STATUS_URL, self.status_form, signature))

        with self._settings_patch():
            with patch("telephony.twilio.api.update_call_log") as mock_update_call_log:
                with patch(
                    "telephony.twilio.api.Twilio.get_twilio_client"
                ) as mock_get_client:
                    with self.assertRaises(frappe.PermissionError):
                        update_call_status_info(**self.status_form)

        mock_update_call_log.assert_not_called()
        mock_get_client.assert_not_called()
