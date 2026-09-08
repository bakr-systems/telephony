import frappe
from frappe import _

from telephony.utils import _get_contact_by_phone_number, parse_call_log


@frappe.whitelist()
def is_call_integration_enabled():
    twilio_enabled = frappe.db.get_single_value("TP Twilio Settings", "enabled")
    exotel_enabled = frappe.db.get_single_value("TP Exotel Settings", "enabled")

    return {
        "twilio_enabled": twilio_enabled,
        "exotel_enabled": exotel_enabled,
        "default_calling_medium": get_user_default_calling_medium(),
    }


@frappe.whitelist()
def set_default_calling_medium(medium):
    if not frappe.db.exists("TP Telephony Agent", frappe.session.user):
        frappe.get_doc(
            {
                "doctype": "TP Telephony Agent",
                "user": frappe.session.user,
                "default_medium": medium,
            }
        ).insert(ignore_permissions=True)
    else:
        frappe.db.set_value(
            "TP Telephony Agent", frappe.session.user, "default_medium", medium
        )

    return get_user_default_calling_medium()


@frappe.whitelist()
def get_contact_by_phone_number(phone_number):
    """Get contact by phone number."""
    return _get_contact_by_phone_number(phone_number)


def get_user_default_calling_medium():
    if not frappe.db.exists("TP Telephony Agent", frappe.session.user):
        return None

    default_medium = frappe.db.get_value(
        "TP Telephony Agent", frappe.session.user, "default_medium"
    )

    if not default_medium:
        return None

    return default_medium


@frappe.whitelist()
def create_call_log(
    id: str,
    telephony_medium: str,
    from_number: str,
    to_number: str,
    duration: float | int | str,
    status: str,
    call_type: str,
    caller: str | None,
    receiver: str | None,
    links: list[dict] | str | None,
):
    call_log = frappe.get_doc(
        {
            "doctype": "TP Call Log",
            "id": id,
            "to": to_number,
            "type": call_type,
            "status": status,
            "telephony_medium": telephony_medium,
            "from": from_number,
            "duration": duration,
            "links": links,
        }
    )
    call_log.check_permission("create")
    actor = receiver if call_type == "Incoming" else caller
    if actor and actor != frappe.session.user:
        frappe.throw(
            _("You can only record calls for yourself"), frappe.PermissionError
        )
    if call_type == "Incoming":
        call_log.receiver = frappe.session.user
    else:
        call_log.caller = frappe.session.user

    # Explicit links must be authorized before the first write. Automatic
    # contact matching is optional and must not attach an unreadable contact.
    for link in call_log.links:
        frappe.get_doc(link.link_doctype, link.link_name).check_permission("read")
    contact_number = from_number if call_type == "Incoming" else to_number
    contact = _get_contact_by_phone_number(contact_number)
    if (
        contact
        and contact.get("name")
        and frappe.has_permission("Contact", ptype="read", doc=contact["name"])
    ):
        call_log.link_with_reference_doc("Contact", contact["name"])

    call_log.insert()

    return call_log


@frappe.whitelist()
def get_call_log(name: str):
    call = frappe.get_cached_doc(
        "TP Call Log",
        name,
        fields=[
            "name",
            "caller",
            "receiver",
            "duration",
            "type",
            "status",
            "from",
            "to",
            "recording_url",
            "creation",
        ],
    )
    call.check_permission("read")
    call = parse_call_log(call.as_dict())
    return call


@frappe.whitelist()
def create_telephony_agent():
    if not frappe.db.exists("TP Telephony Agent", {"user": frappe.session.user}):
        agent = frappe.get_doc(
            {
                "doctype": "TP Telephony Agent",
                "user": frappe.session.user,
            }
        ).insert(ignore_permissions=True)
    else:
        agent = frappe.db.get_value("TP Telephony Agent", {"user": frappe.session.user})

    return agent
