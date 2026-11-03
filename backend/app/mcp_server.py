"""MCP enterprise tool server (FastMCP over stdio).

This process is launched by the backend as an isolated tool boundary. Because
MCP subprocesses receive a clean environment, the backend passes configuration
through explicitly; here we defensively ensure the DB schema/seed exist so a
freshly spawned tool server can always serve requests.
"""
import logging

from mcp.server.fastmcp import FastMCP

from .channels import channel_status
from .config import settings
from .db import init_db, seed_reference_data
from .retrieval import search_knowledge
from .task_store import (
    add_contact,
    compose_and_deliver,
    create_task,
    get_ticket_summary,
    list_contacts,
    list_tasks,
    list_tickets,
    search_contacts,
    send_message,
    send_via_channel,
)

logging.basicConfig(level=logging.INFO)

# Ensure the tool server can serve requests even if started standalone.
init_db()
if settings().seed_on_startup:
    try:
        seed_reference_data()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning('Seed skipped: %s', exc)

mcp = FastMCP('Enterprise Tools')


@mcp.tool()
def create_enterprise_task(title: str, assignee: str = 'Unassigned',
                           due_date: str = 'Not specified', priority: str = 'normal',
                           assignee_email: str = '') -> dict:
    """Create an enterprise task and email the assignee.

    WRITE action. Must only be called after explicit human approval. The
    assignee's email is resolved from the enterprise directory when not given.
    """
    return create_task(title, assignee, due_date, priority, assignee_email)


@mcp.tool()
def list_enterprise_tasks() -> list[dict]:
    """List enterprise tasks, newest first."""
    return list_tasks()


@mcp.tool()
def search_enterprise_contacts(query: str) -> list[dict]:
    """Search the enterprise directory by name, team or email."""
    return search_contacts(query)


@mcp.tool()
def list_enterprise_contacts() -> list[dict]:
    """List all enterprise directory contacts."""
    return list_contacts()


@mcp.tool()
def add_enterprise_contact(name: str, email: str, team: str = 'General',
                           phone: str = '') -> dict:
    """Add or update a directory contact (enables email + WhatsApp routing)."""
    return add_contact(name, email, team, phone)


@mcp.tool()
def compose_and_send_document(doc_type: str, recipient: str = '',
                              instructions: str = '', channels: str = '') -> dict:
    """Generate a document (e.g. leave letter, memo) and deliver it.

    WRITE action. Must only be called after explicit human approval. `channels`
    is a comma list like 'email,whatsapp,teams'; empty uses the configured
    defaults. The recipient's address/number is resolved from the directory.
    """
    return compose_and_deliver(doc_type, recipient, instructions, channels)


@mcp.tool()
def send_quick_message(message: str, recipient: str = '', channels: str = '') -> dict:
    """Send a free-form message to a person across channels (sent as-is, no document).

    WRITE action. Must only be called after explicit human approval. `channels`
    is a comma list like 'whatsapp,email'; empty uses the configured defaults.
    Example: message='Hi!', recipient='David Kim', channels='whatsapp'.
    """
    return send_message(message, recipient, channels)


# --------------------------------------------------------------------------
# Per-channel tools. Each external channel is exposed as its OWN MCP tool, so
# an agent can pick one directly (send_email vs send_whatsapp vs post_to_teams).
# They reuse the same channel layer behind send_quick_message, just scoped to
# a single channel. All are WRITE actions (confirm before calling).
# --------------------------------------------------------------------------
@mcp.tool()
def send_email(recipient: str, body: str, subject: str = '') -> dict:
    """Send an EMAIL to a person. `recipient` is a contact name or an email address."""
    return send_via_channel('email', recipient, subject, body)


@mcp.tool()
def send_whatsapp(recipient: str, message: str) -> dict:
    """Send a WHATSAPP message to a person (needs their phone on the contact)."""
    return send_via_channel('whatsapp', recipient, '', message)


@mcp.tool()
def post_to_teams(message: str, recipient: str = '') -> dict:
    """Post a message to the configured Microsoft TEAMS chat/channel (webhook)."""
    return send_via_channel('teams', recipient, '', message)


@mcp.tool()
def post_to_slack(message: str, recipient: str = '') -> dict:
    """Post a message to the configured SLACK channel (webhook)."""
    return send_via_channel('slack', recipient, '', message)


@mcp.tool()
def list_notification_channels() -> list[dict]:
    """List delivery channels and whether each is configured to send."""
    return channel_status()


@mcp.tool()
def get_enterprise_ticket_summary() -> dict:
    """Summarize incident tickets (totals + serious incidents)."""
    return get_ticket_summary()


@mcp.tool()
def list_enterprise_tickets() -> list[dict]:
    """List all incident tickets."""
    return list_tickets()


@mcp.tool()
def search_enterprise_knowledge(query: str) -> list[dict]:
    """Search enterprise policy/knowledge documents."""
    return search_knowledge(query)


if __name__ == '__main__':
    mcp.run(transport='stdio')
