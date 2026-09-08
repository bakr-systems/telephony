# Copyright (c) 2026, bakr-systems and Contributors
# See license.txt

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from telephony.api import create_call_log, get_call_log
from telephony.exotel.handler import make_a_call


class TestCallAPIAuthorization(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        frappe.set_user("Administrator")
        self.agent = "_test_call_agent@example.com"
        self.other = "_test_call_other@example.com"
        self.unprivileged = "_test_call_unprivileged@example.com"
        for email, roles in (
            (self.agent, ["TP Agent"]),
            (self.other, ["TP Agent"]),
            (self.unprivileged, []),
        ):
            if not frappe.db.exists("User", email):
                frappe.get_doc(
                    {
                        "doctype": "User",
                        "email": email,
                        "first_name": "Call API Test",
                        "send_welcome_email": 0,
                        "roles": [{"role": role} for role in roles],
                    }
                ).insert()
        self.existing = frappe.get_doc(
            {
                "doctype": "TP Call Log",
                "id": frappe.generate_hash(length=20),
                "from": "+12025550101",
                "to": "+12025550102",
                "type": "Outgoing",
                "status": "Completed",
                "caller": self.other,
                "recording_url": "https://example.invalid/private-recording",
            }
        ).insert()
        if frappe.db.exists("TP Telephony Agent", self.agent):
            frappe.delete_doc("TP Telephony Agent", self.agent)
        frappe.get_doc(
            {
                "doctype": "TP Telephony Agent",
                "user": self.agent,
                "mobile_no": "+12025550101",
                "exotel_number": "+12025550103",
            }
        ).insert()
        self.private_note = frappe.get_doc(
            {"doctype": "Note", "title": frappe.generate_hash(length=20), "public": 0}
        ).insert()
        frappe.set_user(self.agent)

    def tearDown(self):
        frappe.db.rollback()
        frappe.set_user("Administrator")
        super().tearDown()

    def call_args(self, **overrides):
        args = dict(
            id=frappe.generate_hash(length=20),
            telephony_medium="Manual",
            from_number="+12025550101",
            to_number="+12025550102",
            duration=30,
            status="Completed",
            call_type="Outgoing",
            caller=frappe.session.user,
            receiver=None,
            links=[],
        )
        args.update(overrides)
        return args

    def test_read_rejects_user_without_document_permission_before_enrichment(self):
        frappe.set_user(self.unprivileged)
        with patch("telephony.api.parse_call_log") as parse:
            with self.assertRaises(frappe.PermissionError):
                get_call_log(self.existing.name)
        parse.assert_not_called()

    def test_read_allows_existing_document_permission(self):
        result = get_call_log(self.existing.name)
        self.assertEqual(result["name"], self.existing.name)
        self.assertEqual(result["recording_url"], self.existing.recording_url)

    def test_create_rejects_user_without_create_permission_without_writes(self):
        frappe.set_user(self.unprivileged)
        args = self.call_args()
        with self.assertRaises(frappe.PermissionError):
            create_call_log(**args)
        self.assertFalse(frappe.db.exists("TP Call Log", args["id"]))

    def test_create_rejects_another_caller_without_writes(self):
        args = self.call_args(caller=self.other)
        with self.assertRaises(frappe.PermissionError):
            create_call_log(**args)
        self.assertFalse(frappe.db.exists("TP Call Log", args["id"]))

    def test_create_rejects_another_receiver_without_writes(self):
        args = self.call_args(call_type="Incoming", caller=None, receiver=self.other)
        with self.assertRaises(frappe.PermissionError):
            create_call_log(**args)
        self.assertFalse(frappe.db.exists("TP Call Log", args["id"]))

    def test_create_rejects_unreadable_explicit_link_without_writes(self):
        args = self.call_args(
            links=[
                {
                    "link_doctype": "Note",
                    "link_name": self.private_note.name,
                }
            ]
        )
        with self.assertRaises(frappe.PermissionError):
            create_call_log(**args)
        self.assertFalse(frappe.db.exists("TP Call Log", args["id"]))

    def test_create_accepts_self_and_readable_explicit_link(self):
        call = create_call_log(
            **self.call_args(
                links=[{"link_doctype": "TP Call Log", "link_name": self.existing.name}]
            )
        )
        stored = frappe.get_doc("TP Call Log", call.name)
        self.assertEqual(stored.caller, self.agent)
        self.assertTrue(stored.has_link("TP Call Log", self.existing.name))

    def test_create_incoming_binds_receiver_when_omitted(self):
        call = create_call_log(**self.call_args(call_type="Incoming", caller=None))
        self.assertEqual(
            frappe.db.get_value("TP Call Log", call.name, "receiver"), self.agent
        )

    def exotel_mocks(self, stack):
        stack.enter_context(
            patch("telephony.exotel.handler.is_integration_enabled", return_value=True)
        )
        endpoint = stack.enter_context(
            patch(
                "telephony.exotel.handler.get_exotel_endpoint",
                return_value="https://example.invalid/calls",
            )
        )
        phones = stack.enter_context(
            patch(
                "telephony.exotel.handler.get_all_exophones",
                return_value=["+12025550103", "+12025550999"],
            )
        )
        stack.enter_context(
            patch(
                "telephony.exotel.handler.get_status_updater_url",
                return_value="https://example.invalid/status",
            )
        )
        response = MagicMock()
        response.json.return_value = {
            "Call": {
                "Sid": "test-provider-call",
                "From": "+12025550101",
                "To": "+12025550102",
            }
        }
        post = stack.enter_context(
            patch("telephony.exotel.handler.requests.post", return_value=response)
        )
        create = stack.enter_context(patch("telephony.exotel.handler.create_call_log"))
        return endpoint, phones, post, create

    def assert_exotel_denied(self, **args):
        with ExitStack() as stack:
            mocks = self.exotel_mocks(stack)
            with self.assertRaises(frappe.PermissionError):
                make_a_call("+12025550102", **args)
            for mock in mocks:
                mock.assert_not_called()

    def test_exotel_rejects_overridden_mobile_before_provider_access(self):
        self.assert_exotel_denied(from_number="+12025550999")

    def test_exotel_rejects_overridden_caller_id_before_provider_access(self):
        self.assert_exotel_denied(caller_id="+12025550999")

    def test_exotel_rejects_user_without_agent_even_with_explicit_numbers(self):
        frappe.set_user(self.other)
        self.assert_exotel_denied(from_number="+12025550101", caller_id="+12025550103")

    def test_exotel_rejects_unprivileged_user_before_provider_access(self):
        frappe.set_user(self.unprivileged)
        self.assert_exotel_denied(from_number="+12025550101", caller_id="+12025550103")

    def test_exotel_rejects_unreadable_link_before_provider_access(self):
        self.assert_exotel_denied(
            link_doctype="Note", link_docname=self.private_note.name
        )

    def test_exotel_uses_own_mapping_and_preserves_matching_explicit_arguments(self):
        for explicit in (
            {},
            {"from_number": "+12025550101", "caller_id": "+12025550103"},
        ):
            with self.subTest(explicit=explicit), ExitStack() as stack:
                _, phones, post, create = self.exotel_mocks(stack)
                result = make_a_call("+12025550102", **explicit)
                self.assertEqual(result["CallSid"], "test-provider-call")
                self.assertEqual(post.call_args.kwargs["data"]["From"], "+12025550101")
                self.assertEqual(
                    post.call_args.kwargs["data"]["CallerId"], "+12025550103"
                )
                self.assertEqual(create.call_args.kwargs["agent"], self.agent)
                phones.assert_called_once()
