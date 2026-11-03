"""Initial reference data seeded into the database on first startup.

Kept in one place so it is easy to change, and so the rest of the code works
purely against the database (dynamic data) rather than in-code constants.

The emails below use the RFC-2606 reserved `example.com` domain. For a live
email demo, edit these to real inboxes you control (or add contacts at runtime
via `POST /contacts`).
"""

SEED_CONTACTS: list[dict] = [
    {'name': 'Alice Johnson', 'team': 'Sales', 'email': 'alice.johnson@example.com', 'phone': '+15551230001'},
    {'name': 'Bob Patel', 'team': 'Operations', 'email': 'bob.patel@example.com', 'phone': '+15551230002'},
    {'name': 'Carla Gomez', 'team': 'Support', 'email': 'carla.gomez@example.com', 'phone': '+15551230003'},
    {'name': 'David Kim', 'team': 'Engineering', 'email': 'david.kim@example.com', 'phone': '+15551230004'},
    {'name': 'Emma Williams', 'team': 'Finance', 'email': 'emma.williams@example.com', 'phone': '+15551230005'},
]

SEED_TICKETS: list[dict] = [
    {'public_id': 'INC-1001', 'title': 'Payment gateway timeout', 'severity': 'critical', 'status': 'open', 'owner': 'Operations'},
    {'public_id': 'INC-1002', 'title': 'Customer cannot reset password', 'severity': 'high', 'status': 'open', 'owner': 'Support'},
    {'public_id': 'INC-1003', 'title': 'Invoice export formatting', 'severity': 'medium', 'status': 'resolved', 'owner': 'Finance'},
    {'public_id': 'INC-1004', 'title': 'Dashboard loading slowly', 'severity': 'low', 'status': 'open', 'owner': 'Engineering'},
]

SEED_KNOWLEDGE: list[dict] = [
    {'doc_key': 'security-001', 'title': 'Sensitive actions',
     'text': 'Creating, deleting, sending, changing permissions, or modifying enterprise records requires explicit user confirmation before execution.'},
    {'doc_key': 'incident-001', 'title': 'Incident escalation',
     'text': 'Critical incidents should be escalated to the on-call operations team. High-severity incidents should be reviewed by the service owner.'},
    {'doc_key': 'access-001', 'title': 'Access control',
     'text': 'Agents must use least privilege and must not bypass authentication, authorization, or approval controls.'},
    {'doc_key': 'support-001', 'title': 'Support workflow',
     'text': 'Support tickets should include a clear summary, affected service, severity, and owner before escalation.'},
    {'doc_key': 'task-001', 'title': 'Task assignment',
     'text': 'When a task is created and assigned to an employee, the assignee is notified by email with the task title, due date and priority.'},
]
