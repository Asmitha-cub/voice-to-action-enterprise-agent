import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .agent import confirm_execution, plan_command
from .config import settings
from .db import Execution, init_db, seed_reference_data, session_scope
from .mcp_client import start_mcp, stop_mcp
from .channels import channel_status
from .models import (
    CommandResponse,
    ComposeRequest,
    ConfirmRequest,
    ContactRequest,
    MessageRequest,
    ResolveRequest,
    TextCommandRequest,
)
from .security import require_api_key
from .task_store import (
    add_contact,
    compose_and_deliver,
    list_contacts,
    list_documents,
    list_tasks,
    list_tickets,
    preview_recipients,
    send_message,
)
from .transcription import transcribe_audio

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings().seed_on_startup:
        try:
            seed_reference_data()
        except Exception as exc:  # noqa: BLE001
            log.warning('Reference data seed skipped: %s', exc)
    try:
        await start_mcp()
    except Exception as exc:  # noqa: BLE001
        log.warning('MCP client failed to pre-start (will retry on first call): %s', exc)
    yield
    await stop_mcp()


app = FastAPI(
    title=settings().app_name,
    version='2.0.0',
    description='Production-oriented voice-enabled enterprise action agent',
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().allowed_origins_list,
    allow_credentials=True,
    allow_methods=['GET', 'POST'],
    allow_headers=['*'],
)


@app.get('/health')
def health():
    channels = channel_status()
    return {'status': 'ok', 'service': settings().app_name,
            'environment': settings().environment,
            'email_configured': settings().smtp_configured,
            'default_channels': settings().default_channels_list,
            'channels': channels,
            'channels_ready': [c['channel'] for c in channels if c['configured']]}


@app.post('/commands/text', response_model=CommandResponse, dependencies=[Depends(require_api_key)])
async def run_text_command(request: TextCommandRequest):
    plan, eid, result, activity = await plan_command(request.command)
    return CommandResponse(transcript=request.command, plan=plan,
                           execution_id=eid, result=result, activity=activity)


@app.post('/commands/audio', response_model=CommandResponse, dependencies=[Depends(require_api_key)])
async def run_audio_command(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, 'An audio filename is required.')
    audio = await file.read()
    if len(audio) > settings().max_audio_mb * 1024 * 1024:
        raise HTTPException(413, f'Audio exceeds {settings().max_audio_mb} MB')
    try:
        transcript = transcribe_audio(file.filename, audio)
        plan, eid, result, activity = await plan_command(transcript)
        return CommandResponse(transcript=transcript, plan=plan,
                               execution_id=eid, result=result, activity=activity)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception('Audio processing failed')
        raise HTTPException(502, 'Speech-to-text service failed.') from exc


@app.post('/commands/confirm', dependencies=[Depends(require_api_key)])
async def confirm_action(request: ConfirmRequest):
    try:
        return await confirm_execution(request.execution_id, request.approved)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/tasks', dependencies=[Depends(require_api_key)])
def get_tasks():
    return {'tasks': list_tasks()}


@app.get('/contacts', dependencies=[Depends(require_api_key)])
def get_contacts():
    return {'contacts': list_contacts()}


@app.post('/contacts', dependencies=[Depends(require_api_key)])
def create_contact(request: ContactRequest):
    try:
        return add_contact(request.name, str(request.email), request.team, request.phone)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/channels', dependencies=[Depends(require_api_key)])
def get_channels():
    """Delivery channels and whether each is configured to send."""
    return {'default_channels': settings().default_channels_list,
            'channels': channel_status()}


@app.get('/documents', dependencies=[Depends(require_api_key)])
def get_documents():
    """Documents the agent has generated, with their delivery status."""
    return {'documents': list_documents()}


@app.post('/compose', dependencies=[Depends(require_api_key)])
def compose_document_now(request: ComposeRequest):
    """Generate a document and deliver it directly (bypasses the planner).

    Useful for the Compose & Send panel and for testing multi-channel delivery.
    """
    try:
        return compose_and_deliver(request.doc_type, request.recipient,
                                   request.instructions, request.channels)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/send', dependencies=[Depends(require_api_key)])
def send_message_now(request: MessageRequest):
    """Send a free-form message as-is across channels (no document generation)."""
    try:
        return send_message(request.message, request.recipient, request.channels)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/resolve', dependencies=[Depends(require_api_key)])
def resolve_preview(request: ResolveRequest):
    """Preview who a recipient string resolves to (names + real emails/phones),
    so the UI can confirm before sending. Does not send anything."""
    return {'recipients': preview_recipients(request.recipient)}


@app.get('/tickets', dependencies=[Depends(require_api_key)])
def get_tickets():
    return {'tickets': list_tickets()}


@app.get('/executions', dependencies=[Depends(require_api_key)])
def list_executions(limit: int = 25):
    with session_scope() as db:
        from sqlalchemy import select
        rows = db.scalars(select(Execution).order_by(Execution.created_at.desc()).limit(limit)).all()
        return {'executions': [{
            'id': e.id, 'action': e.action, 'status': e.status,
            'parameters': e.parameters, 'error': e.error,
            'created_at': e.created_at.isoformat() if e.created_at else None,
        } for e in rows]}


@app.get('/executions/{execution_id}', dependencies=[Depends(require_api_key)])
def get_execution(execution_id: str):
    with session_scope() as db:
        e = db.get(Execution, execution_id)
        if not e:
            raise HTTPException(404, 'Execution not found.')
        return {'id': e.id, 'action': e.action, 'status': e.status,
                'parameters': e.parameters, 'result': e.result, 'error': e.error,
                'created_at': e.created_at.isoformat() if e.created_at else None}
