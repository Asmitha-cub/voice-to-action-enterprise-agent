import asyncio
import logging
import re
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .config import settings
from .db import Execution, audit, session_scope
from .llm import plan_with_llm
from .mcp_client import call_tool
from .models import ActionPlan

log = logging.getLogger(__name__)

ALLOWED = {'create_task', 'list_tasks', 'search_contacts',
           'ticket_summary', 'search_knowledge', 'compose_document',
           'send_message', 'unknown'}
WRITE = {'create_task', 'compose_document', 'send_message'}

TOOL_NAMES = {
    'create_task': 'create_enterprise_task',
    'list_tasks': 'list_enterprise_tasks',
    'search_contacts': 'search_enterprise_contacts',
    'ticket_summary': 'get_enterprise_ticket_summary',
    'search_knowledge': 'search_enterprise_knowledge',
    'compose_document': 'compose_and_send_document',
    'send_message': 'send_quick_message',
}


class AgentState(TypedDict, total=False):
    command: str
    action: str
    parameters: dict[str, str]
    summary: str
    plan: ActionPlan
    activity: list[str]


# ----------------------------------------------------------- deterministic NLP
def _extract_task_fields(text: str) -> dict[str, str]:
    """Best-effort structured extraction used when no LLM is configured.

    Fixes the earlier parser that produced values like assignee="Karla by" and
    due_date="date tomorrow".
    """
    title = text.strip(' .!?,')
    fields: dict[str, str] = {}

    # priority
    m = (re.search(r'\b(high|low|urgent|normal)\s+priority\b', title, re.I)
         or re.search(r'\bpriority[:\s]+(high|low|urgent|normal)\b', title, re.I))
    if m:
        p = m.group(1).lower()
        fields['priority'] = 'high' if p == 'urgent' else p
        title = (title[:m.start()] + ' ' + title[m.end():]).strip(' .!?,')

    # due date (trailing "by/due/due date/before/on <x>")
    m = re.search(r'\b(?:by|due(?:\s+date)?|before|on)\s+(.+?)\s*$', title, re.I)
    if m:
        fields['due_date'] = m.group(1).strip(' .!?,')
        title = title[:m.start()].strip(' .!?,')

    # assignee (trailing "for/to/assigned to <Capitalized Name>")
    m = re.search(
        r'\b(?:assigned to|assign to|for|to)\s+'
        r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2})\s*$",
        title,
    )
    if m:
        fields['assignee'] = m.group(1).strip()
        title = title[:m.start()].strip(' .!?,')

    # explicit email anywhere in the command
    m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
    if m:
        fields['assignee_email'] = m.group(0)

    # strip a leading command phrase e.g. "create a task to ...", "please add task to ..."
    title = re.sub(
        r'^\s*(?:please\s+)?'
        r'(?:can you\s+)?'
        r'(?:create|add|make|new|open|log|raise|set up|schedule)\s+'
        r'(?:a\s+|an\s+|the\s+)?(?:new\s+)?task\s+'
        r'(?:to|for|that|which|about|called|named|titled|:)?\s*',
        '', title, flags=re.I,
    ).strip(' .!?,:')
    # drop trailing filler left over after assignee/due removal
    title = re.sub(r'\b(?:and|then)\s+(?:assign|send|notify|email|share)\b.*$', '',
                   title, flags=re.I).strip(' .!?,:')
    if title:
        title = title[0].upper() + title[1:]

    fields['title'] = title
    return fields


_CHANNEL_WORDS = {
    'email': 'email', 'mail': 'email', 'e-mail': 'email', 'gmail': 'email',
    'whatsapp': 'whatsapp', "whats app": 'whatsapp', 'wa': 'whatsapp',
    'teams': 'teams', 'microsoft teams': 'teams', 'slack': 'slack',
}

# Specific document phrases. Deliberately excludes generic words like "report"
# or "summary" that collide with task creation ("prepare the sales report") and
# the ticket summary action.
_DOC_WORDS = ['leave letter', 'leave application', 'offer letter', 'resignation letter',
              'cover letter', 'recommendation letter', 'appointment letter', 'warning letter',
              'apology letter', 'invitation letter', 'letter', 'memo', 'notice',
              'announcement', 'email draft']


def _extract_compose_fields(command: str) -> dict[str, str]:
    """Pull doc_type, recipient, channels and instructions from a free command."""
    fields: dict[str, str] = {}
    lower = command.lower()

    # document type: the most specific known phrase present, else 'letter'
    doc_type = next((w for w in _DOC_WORDS if w in lower), 'letter')
    fields['doc_type'] = doc_type

    # channels mentioned anywhere (e.g. "send on whatsapp and email")
    found: list[str] = []
    for word, canonical in _CHANNEL_WORDS.items():
        if re.search(r'\b' + re.escape(word) + r'\b', lower) and canonical not in found:
            found.append(canonical)
    if found:
        fields['channels'] = ','.join(found)

    # recipient: an email address wins, else the name after "for"/"to".
    em = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', command)
    if em:
        fields['recipient'] = em.group(0)
    else:
        m = re.search(
            r'\b(?:for|to)\s+(.+?)'
            r'(?:\s+(?:on|via|using|through|and\s+send|saying)\b|[:,.]|$)',
            command, re.I)
        if m:
            name = re.sub(
                r'\b(?:on\s+)?(?:email|e-?mail|mail|whatsapp|whats app|wa|teams|'
                r'microsoft teams|slack)\b.*$', '', m.group(1), flags=re.I).strip(' .,:-')
            if name:
                fields['recipient'] = name

    # instructions: keep the whole command as context for the generator
    fields['instructions'] = command.strip()
    return fields


def _extract_message_fields(command: str) -> dict[str, str]:
    """Pull message text, recipient and channels from a 'send X on Y to Z' command."""
    fields: dict[str, str] = {}
    lower = command.lower()

    found: list[str] = []
    for word, canonical in _CHANNEL_WORDS.items():
        if re.search(r'\b' + re.escape(word) + r'\b', lower) and canonical not in found:
            found.append(canonical)
    if found:
        fields['channels'] = ','.join(found)

    # Recipient: an email address wins; otherwise the name after "to"/"for",
    # allowing lowercase (voice transcripts are often lowercased).
    em = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', command)
    if em:
        fields['recipient'] = em.group(0)
    else:
        m = re.search(
            r'\b(?:to|for)\s+(.+?)'
            r'(?:\s+(?:on|via|using|through|saying|that\s+says|message|msg|text)\b|[:,.]|$)',
            command, re.I)
        if m:
            name = re.sub(
                r'\b(?:on\s+)?(?:email|e-?mail|mail|whatsapp|whats app|wa|teams|'
                r'microsoft teams|slack)\b.*$', '', m.group(1), flags=re.I).strip(' .,:-')
            if name:
                fields['recipient'] = name

    # Message text. Prefer an explicit marker: "saying ...", or "message: ..." with
    # a colon (a bare "message" is just the noun in "send a message to ...").
    m = (re.search(r'\b(?:saying|that says)\s+(.+)$', command, re.I)
         or re.search(r'\b(?:message|msg|text|note)\s*[:\-]\s*(.+)$', command, re.I))
    if m and m.group(1).strip():
        msg = m.group(1).strip()
    else:
        # Otherwise strip the leading verb and the recipient phrase.
        msg = re.sub(r'^\s*(?:please\s+)?(?:send|message|text|ping|notify|whatsapp|email|mail)\b',
                     '', command, flags=re.I)
        msg = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '', msg)  # drop an email address
        msg = re.sub(r'\b(?:to|for)\s+[A-Za-z][\w.\'-]*(?:\s+[A-Za-z][\w.\'-]*){0,2}',
                     '', msg, flags=re.I)
        msg = re.sub(r'\b(?:a|an|the)\s+(?:message|text|msg|note)\b', '', msg, flags=re.I)
    # Always trim a trailing channel clause ("... on email") and tidy up.
    msg = re.sub(r'\bon\s+(?:whatsapp|whats app|wa|e-?mail|email|mail|teams|microsoft teams|slack)\b.*$',
                 '', msg, flags=re.I).strip(' .,:-')
    fields['message'] = msg or 'Hi'
    return fields


def fallback_plan(command: str) -> dict:
    lower = command.lower()

    # send a quick message: "send hi on whatsapp to David" (needs an explicit channel,
    # and must not be a document or a task).
    _has_channel = any(re.search(r'\b' + re.escape(w) + r'\b', lower) for w in _CHANNEL_WORDS)
    if 'task' not in lower and not any(w in lower for w in _DOC_WORDS) and _has_channel and \
            re.search(r'\b(send|message|text|ping|notify|whatsapp|email)\b', lower):
        fields = _extract_message_fields(command)
        who = fields.get('recipient', 'the recipient')
        chans = fields.get('channels', 'default channels')
        return {
            'action': 'send_message',
            'parameters': fields,
            'summary': f"Send a message to {who} via {chans}.",
        }

    # compose-and-deliver: "write/draft/prepare/generate a <letter/memo> ... and send it".
    # Guarded so task commands ("create a task ...") never fall in here.
    if 'task' not in lower and \
            re.search(r'\b(write|draft|prepare|compose|generate|create|make)\b', lower) and \
            any(w in lower for w in _DOC_WORDS):
        fields = _extract_compose_fields(command)
        who = fields.get('recipient', 'the recipient')
        chans = fields.get('channels', 'default channels')
        return {
            'action': 'compose_document',
            'parameters': fields,
            'summary': f"Generate a {fields['doc_type']} for {who} and send via {chans}.",
        }

    if re.search(r'\b(create|add|make|open|set up)\s+(a\s+|an\s+|the\s+|new\s+)*tasks?\b', lower) \
            or re.search(r'\b(create|add|make)\b.*\btask\b', lower):
        remainder = re.sub(
            r'^.*?\b(create|add|make|open|set up)\s+(a\s+|an\s+|the\s+|new\s+)*tasks?\s*(to\s+|for\s+|:\s*)?',
            '', command, flags=re.I,
        ).strip()
        if not remainder:
            remainder = command
        fields = _extract_task_fields(remainder)
        assignee = fields.get('assignee', 'Unassigned')
        due = fields.get('due_date', 'Not specified')
        return {
            'action': 'create_task',
            'parameters': fields,
            'summary': f"Create task '{fields.get('title', '')}' for {assignee} (due: {due}).",
        }

    if any(x in lower for x in ['ticket', 'incident']):
        return {'action': 'ticket_summary', 'parameters': {},
                'summary': 'Retrieve enterprise incident totals and serious incidents.'}

    if any(x in lower for x in ['policy', 'procedure', 'approval', 'security',
                                'access', 'how should', 'knowledge']):
        return {'action': 'search_knowledge', 'parameters': {'query': command},
                'summary': 'Search enterprise knowledge for a policy or procedure.'}

    if any(x in lower for x in ['contact', 'employee', 'directory', 'people', 'email of', 'who is']) \
            or re.search(r'\b(find|search|lookup|look up)\b', lower):
        q = re.sub(r'^\s*(find|search|lookup|look up)\s*', '', command, flags=re.I)
        q = re.sub(r'\b(for|the|contacts?|employees?|directory|people|email|of)\b', ' ', q, flags=re.I)
        q = ' '.join(q.split())
        return {'action': 'search_contacts', 'parameters': {'query': q or command},
                'summary': f"Search enterprise directory for '{q}'."}

    if 'task' in lower or 'todo' in lower or 'to-do' in lower:
        return {'action': 'list_tasks', 'parameters': {},
                'summary': 'Retrieve tasks from the enterprise task system.'}

    return {'action': 'unknown', 'parameters': {},
            'summary': 'I could not map that instruction to an allowed enterprise action.'}


# ------------------------------------------------------------- LangGraph nodes
def understand(state: AgentState) -> AgentState:
    activity = state.get('activity', []) + ['Received command', 'Validated command as untrusted input']
    data = plan_with_llm(state['command']) or fallback_plan(state['command'])
    action = data.get('action', 'unknown')
    action = action if action in ALLOWED else 'unknown'
    params = {str(k): str(v) for k, v in (data.get('parameters') or {}).items()
              if v is not None and str(v).strip()}
    return {**state, 'action': action, 'parameters': params,
            'summary': str(data.get('summary') or ''),
            'activity': activity + ['Intent identified by planner']}


def validate(state: AgentState) -> AgentState:
    action = state['action']
    p = state.get('parameters', {})
    activity = state.get('activity', []) + ['Validated action against allowlist']

    if action == 'create_task' and not p.get('title'):
        plan = ActionPlan(action=action, summary='Task creation needs a task title.',
                          parameters=p, requires_confirmation=False,
                          status='needs_clarification', risk='medium')
    elif action == 'compose_document' and not p.get('doc_type'):
        plan = ActionPlan(action=action,
                          summary='Tell me what to write (e.g. a leave letter) and for whom.',
                          parameters=p, requires_confirmation=False,
                          status='needs_clarification', risk='medium')
    elif action == 'send_message' and not p.get('message'):
        plan = ActionPlan(action=action,
                          summary='Tell me the message to send, to whom, and on which channel.',
                          parameters=p, requires_confirmation=False,
                          status='needs_clarification', risk='medium')
    elif action == 'search_contacts' and not p.get('query'):
        plan = ActionPlan(action=action, summary='Tell me the employee name, team, or email to search.',
                          parameters=p, requires_confirmation=False,
                          status='needs_clarification', risk='low')
    elif action == 'unknown':
        plan = ActionPlan(action='unknown',
                          summary='I can create/list tasks, search contacts, summarize incidents, or search enterprise policies.',
                          parameters={}, requires_confirmation=False,
                          status='needs_clarification', risk='low')
    else:
        risk = 'high' if action in WRITE else 'low'
        default_summary = {
            'create_task': 'Create enterprise task.',
            'list_tasks': 'List enterprise tasks.',
            'search_contacts': 'Search enterprise directory.',
            'ticket_summary': 'Summarize enterprise incidents.',
            'search_knowledge': 'Search enterprise knowledge.',
            'compose_document': 'Generate a document and deliver it across channels.',
            'send_message': 'Send a message to a person across channels.',
        }.get(action, 'Execute enterprise action.')
        plan = ActionPlan(action=action, summary=state.get('summary') or default_summary,
                          parameters=p, requires_confirmation=action in WRITE,
                          status='planned', risk=risk)

    return {**state, 'plan': plan, 'activity': activity + ['Validated parameters and risk']}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node('understand', understand)
    g.add_node('validate', validate)
    g.add_edge(START, 'understand')
    g.add_edge('understand', 'validate')
    g.add_edge('validate', END)
    return g.compile()


GRAPH = build_graph()


# ------------------------------------------------------------- tool execution
def _tool_args(plan: ActionPlan) -> dict:
    p = plan.parameters
    if plan.action == 'create_task':
        args = {'title': p.get('title', '')}
        for key in ('assignee', 'due_date', 'priority', 'assignee_email'):
            if p.get(key):
                args[key] = p[key]
        return args
    if plan.action == 'compose_document':
        args = {'doc_type': p.get('doc_type', 'letter')}
        for key in ('recipient', 'instructions', 'channels'):
            if p.get(key):
                args[key] = p[key]
        return args
    if plan.action == 'send_message':
        args = {'message': p.get('message', '')}
        for key in ('recipient', 'channels'):
            if p.get(key):
                args[key] = p[key]
        return args
    if plan.action in ('search_contacts', 'search_knowledge'):
        return {'query': p.get('query', '')}
    return {}


async def _execute(plan: ActionPlan, execution_id: str) -> dict:
    audit('tool_execution_started', {'action': plan.action}, execution_id)
    tool_name = TOOL_NAMES[plan.action]
    args = _tool_args(plan)
    last: dict = {'error': 'unknown error'}
    for attempt in range(settings().max_retries + 1):
        try:
            result = await call_tool(tool_name, args)
            if not (isinstance(result, dict) and result.get('error')):
                audit('tool_execution_completed', {'attempt': attempt + 1}, execution_id)
                return result if isinstance(result, dict) else {'data': result}
            last = result
        except Exception as exc:  # noqa: BLE001
            last = {'error': str(exc)}
        if attempt < settings().max_retries:
            await asyncio.sleep(0.5 * (attempt + 1))
    audit('tool_execution_failed', last, execution_id)
    return last


# ------------------------------------------------------------------ lifecycle
async def plan_command(command: str):
    result = GRAPH.invoke({'command': command})
    plan: ActionPlan = result['plan']
    execution_id = str(uuid.uuid4())

    with session_scope() as db:
        db.add(Execution(
            id=execution_id, command=command, action=plan.action,
            status='awaiting_approval' if plan.requires_confirmation else 'running',
            requires_confirmation=plan.requires_confirmation, parameters=plan.parameters,
        ))
    audit('plan_created', {'action': plan.action, 'risk': plan.risk}, execution_id)

    if plan.status == 'needs_clarification':
        with session_scope() as db:
            db.get(Execution, execution_id).status = 'needs_clarification'
        return plan, None, None, result.get('activity', [])

    if plan.requires_confirmation:
        return plan, execution_id, None, \
            result.get('activity', []) + ['Waiting for explicit human approval']

    output = await _execute(plan, execution_id)
    status = 'failed' if isinstance(output, dict) and output.get('error') else 'completed'
    with session_scope() as db:
        e = db.get(Execution, execution_id)
        e.status = status
        e.result = output
        e.error = output.get('error') if isinstance(output, dict) else None
    return plan, None, output, result.get('activity', []) + ['Execution finished']


async def confirm_execution(execution_id: str, approved: bool):
    with session_scope() as db:
        e = db.get(Execution, execution_id)
        row = None if e is None else {'status': e.status, 'action': e.action, 'parameters': e.parameters}

    if not row:
        raise ValueError('Approval request was not found or has expired.')
    if row['status'] != 'awaiting_approval':
        raise ValueError('This execution is no longer awaiting approval.')

    if not approved:
        with session_scope() as db:
            db.get(Execution, execution_id).status = 'cancelled'
        audit('human_approval_denied', {}, execution_id)
        return {'message': 'Action cancelled. No write tool was called.', 'status': 'cancelled'}

    plan = ActionPlan(action=row['action'], summary='Approved enterprise action.',
                      parameters=row['parameters'], requires_confirmation=True,
                      status='planned', risk='high')
    with session_scope() as db:
        db.get(Execution, execution_id).status = 'running'
    audit('human_approval_granted', {}, execution_id)

    result = await _execute(plan, execution_id)
    failed = isinstance(result, dict) and result.get('error')
    with session_scope() as db:
        e = db.get(Execution, execution_id)
        e.status = 'failed' if failed else 'completed'
        e.result = result
        e.error = result.get('error') if isinstance(result, dict) else None
    return {'status': 'failed' if failed else 'completed', 'result': result}
