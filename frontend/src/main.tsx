import { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const API_KEY = import.meta.env.VITE_API_KEY ?? ''

type Plan = {
  action: string; summary: string; parameters: Record<string, string>
  requires_confirmation: boolean; status: string; risk: string
}
type EmailStatus = { sent: boolean; reason?: string; to?: string }
type ChannelResult = { channel: string; sent: boolean; reason?: string; to?: string }
type Delivery = { requested?: string[]; any_sent?: boolean; channels?: ChannelResult[] }
type ResponseData = {
  transcript: string; plan: Plan; execution_id?: string
  result?: Record<string, unknown>; activity: string[]
}
type Task = {
  id: string; title: string; assignee: string; assignee_email?: string | null
  due_date: string; priority: string; status: string
  notified?: boolean; notification_status?: string | null
}
type Contact = { name: string; team: string; email: string; phone?: string | null }
type Ticket = { id: string; title: string; severity: string; status: string; owner: string }

function headers(): Record<string, string> {
  return API_KEY ? { 'X-API-Key': API_KEY } : {}
}
async function parse(res: Response) {
  const body = await res.json()
  if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `Request failed (${res.status})`)
  return body
}

function Badge({ text, tone }: { text: string; tone: 'red' | 'green' | 'amber' | 'slate' }) {
  const tones = {
    red: 'bg-rose-500/15 text-rose-300', green: 'bg-emerald-500/15 text-emerald-300',
    amber: 'bg-amber-500/15 text-amber-300', slate: 'bg-slate-500/15 text-slate-300',
  }
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${tones[tone]}`}>{text}</span>
}

function App() {
  const [command, setCommand] = useState('Create a task to prepare the sales report for Alice Johnson by tomorrow')
  const [data, setData] = useState<ResponseData | null>(null)
  const [status, setStatus] = useState('Ready')
  const [busy, setBusy] = useState(false)
  const [recording, setRecording] = useState(false)
  const [autoVoice, setAutoVoice] = useState(true)
  const [tab, setTab] = useState<'tasks' | 'contacts' | 'tickets'>('tasks')
  const [tasks, setTasks] = useState<Task[]>([])
  const [contacts, setContacts] = useState<Contact[]>([])
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [newContact, setNewContact] = useState({ name: '', email: '', team: '', phone: '' })
  // Compose & Send panel state
  const [sendMode, setSendMode] = useState<'asis' | 'ai'>('asis')
  const [sendRecipient, setSendRecipient] = useState('')
  const [sendDocType, setSendDocType] = useState('leave letter')
  const [sendText, setSendText] = useState('')
  const [sendChannels, setSendChannels] = useState<Record<string, boolean>>(
    { email: true, whatsapp: false, teams: false, slack: false })
  const [sendResult, setSendResult] = useState<Delivery | null>(null)
  const [extraRecipient, setExtraRecipient] = useState('')
  type Pending = { recipient: string; channels: string; text: string; mode: 'asis' | 'ai'; docType: string; preview: { name: string; email: string; phone: string }[] }
  const [pending, setPending] = useState<Pending | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])

  async function refreshData() {
    try {
      const [t, c, k] = await Promise.all([
        fetch(`${API}/tasks`, { headers: headers() }).then(parse),
        fetch(`${API}/contacts`, { headers: headers() }).then(parse),
        fetch(`${API}/tickets`, { headers: headers() }).then(parse),
      ])
      setTasks(t.tasks ?? []); setContacts(c.contacts ?? []); setTickets(k.tickets ?? [])
    } catch { /* backend may be starting */ }
  }
  useEffect(() => { void refreshData() }, [])

  async function runText() {
    if (command.trim().length < 3) { setStatus('Enter at least 3 characters.'); return }
    setBusy(true); setStatus('Agent is understanding and planning…')
    try {
      const res = await fetch(`${API}/commands/text`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() },
        body: JSON.stringify({ command }),
      })
      const d = await parse(res); setData(d); setStatus('Plan ready')
      if (!d.execution_id) await refreshData()
    } catch (e) { setStatus(e instanceof Error ? e.message : 'Request failed') } finally { setBusy(false) }
  }

  async function sendAudio(blob: Blob) {
    setBusy(true); setStatus('Transcribing voice and planning workflow…')
    const form = new FormData(); form.append('file', blob, 'voice-command.webm')
    try {
      const res = await fetch(`${API}/commands/audio`, { method: 'POST', headers: headers(), body: form })
      const d = await parse(res); setCommand(d.transcript); setData(d)
      // Auto-run: if the voice command needs approval and auto-run is on, execute
      // it immediately — no second click. Typed commands still ask for approval.
      if (d.execution_id && autoVoice) {
        setStatus(`Heard: "${d.transcript}" — running automatically…`)
        await confirm(true, d.execution_id)
      } else {
        setStatus('Plan ready')
        if (!d.execution_id) await refreshData()
      }
    } catch (e) { setStatus(e instanceof Error ? e.message : 'Voice request failed') } finally { setBusy(false) }
  }

  async function record() {
    if (recording) { recorder.current?.stop(); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = new MediaRecorder(stream); chunks.current = []
      r.ondataavailable = e => chunks.current.push(e.data)
      r.onstop = () => {
        stream.getTracks().forEach(t => t.stop()); setRecording(false)
        void sendAudio(new Blob(chunks.current, { type: 'audio/webm' }))
      }
      recorder.current = r; r.start(); setRecording(true)
      setStatus('Recording… click Stop when finished')
    } catch { setStatus('Microphone permission was not granted.') }
  }

  async function confirm(approved: boolean, executionId?: string) {
    const eid = executionId ?? data?.execution_id
    if (!eid) return
    setBusy(true); setStatus(approved ? 'Executing approved action…' : 'Cancelling…')
    try {
      const res = await fetch(`${API}/commands/confirm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() },
        body: JSON.stringify({ execution_id: eid, approved }),
      })
      const body = await parse(res)
      setData(prev => (prev ? { ...prev, execution_id: undefined, result: body.result ?? body } : prev))
      const delivery = body.result?.email as EmailStatus | undefined
      const executionStatus = body.status === 'cancelled' ? 'Cancelled'
        : delivery?.sent ? `Emailed to ${delivery.to ?? 'recipient'}`
        : delivery?.reason ? `Done — email not sent: ${delivery.reason}`
        : 'Done — executed.'
      setStatus(executionStatus)
      await refreshData(); setTab('tasks')
    } catch (e) { setStatus(e instanceof Error ? e.message : 'Action failed') } finally { setBusy(false) }
  }

  async function addContact() {
    if (!newContact.name || !newContact.email) { setStatus('Contact needs a name and email.'); return }
    setBusy(true)
    try {
      await fetch(`${API}/contacts`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() },
        body: JSON.stringify({
          name: newContact.name, email: newContact.email,
          team: newContact.team || 'General', phone: newContact.phone || '',
        }),
      }).then(parse)
      setNewContact({ name: '', email: '', team: '', phone: '' }); setStatus('Contact added'); await refreshData()
    } catch (e) { setStatus(e instanceof Error ? e.message : 'Could not add contact') } finally { setBusy(false) }
  }

  // Step 1: resolve who it will go to and show a confirmation (no send yet).
  async function doSend() {
    const chans = Object.entries(sendChannels).filter(([, on]) => on).map(([k]) => k).join(',')
    const recipientStr = [sendRecipient, extraRecipient].map(s => s.trim()).filter(Boolean).join(', ')
    if (!sendText.trim()) { setStatus('Enter a message or instructions first.'); return }
    if (!chans) { setStatus('Pick at least one channel.'); return }
    if (!recipientStr) { setStatus('Pick a contact or type an email.'); return }
    setBusy(true); setSendResult(null); setStatus('Resolving recipients…')
    try {
      const res = await fetch(`${API}/resolve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() },
        body: JSON.stringify({ recipient: recipientStr }),
      }).then(parse)
      const preview = (res.recipients ?? []) as { name: string; email: string; phone: string }[]
      setPending({ recipient: recipientStr, channels: chans, text: sendText, mode: sendMode, docType: sendDocType, preview })
      setStatus('Review who this will go to, then Confirm.')
    } catch (e) { setStatus(e instanceof Error ? e.message : 'Could not resolve recipients') } finally { setBusy(false) }
  }

  // Step 2: actually send exactly what was previewed.
  async function confirmSend() {
    if (!pending) return
    setBusy(true); setSendResult(null); setStatus(pending.mode === 'ai' ? 'Generating and sending…' : 'Sending…')
    try {
      const url = pending.mode === 'ai' ? `${API}/compose` : `${API}/send`
      const body = pending.mode === 'ai'
        ? { doc_type: pending.docType || 'letter', recipient: pending.recipient, instructions: pending.text, channels: pending.channels }
        : { message: pending.text, recipient: pending.recipient, channels: pending.channels }
      const res = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() },
        body: JSON.stringify(body),
      }).then(parse)
      const d = (res.delivery ?? null) as Delivery | null
      setSendResult(d); setPending(null)
      setStatus(d?.any_sent ? 'Delivered.' : 'Processed — see per-channel result below.')
      await refreshData()
    } catch (e) { setStatus(e instanceof Error ? e.message : 'Send failed') } finally { setBusy(false) }
  }

  const email = (data?.result?.email ?? null) as EmailStatus | null
  const delivery = ((data?.result as { delivery?: Delivery } | undefined)?.delivery ?? null) as Delivery | null
  const composedDoc = (data?.result as { document?: { title?: string; recipient?: string; body_text?: string } } | undefined)?.document ?? null
  const sevTone = (s: string) => (s === 'critical' || s === 'high' ? 'red' : s === 'medium' ? 'amber' : 'slate')

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-6xl px-5 py-8 md:px-8">
        <header className="mb-8 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="mb-2 text-sm font-semibold uppercase tracking-[.2em] text-cyan-400">GenAI Engineering Sprint</div>
            <h1 className="text-3xl font-bold md:text-5xl">Voice-to-Action Enterprise Agent</h1>
            <p className="mt-3 max-w-2xl text-slate-400">Voice → intent → plan → risk gate → human approval → MCP tool → email + auditable result.</p>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-900 px-4 py-3 text-sm">
            <span className="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-400" /> {status}
          </div>
        </header>

        <div className="grid gap-6 lg:grid-cols-[1.4fr_.8fr]">
          <section className="rounded-3xl border border-slate-800 bg-slate-900 p-6 shadow-2xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-xl font-semibold">Command</h2>
              <span className="text-xs text-slate-500">Protected enterprise action flow</span>
            </div>
            <textarea
              className="min-h-36 w-full resize-y rounded-2xl border border-slate-700 bg-slate-950 p-4 outline-none ring-cyan-400 focus:ring-2"
              value={command} onChange={e => setCommand(e.target.value)} />
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button disabled={busy} onClick={() => void runText()}
                className="rounded-xl bg-cyan-500 px-5 py-3 font-semibold text-slate-950 hover:bg-cyan-400">Plan command</button>
              <button disabled={busy} onClick={() => void record()}
                className={`rounded-xl px-5 py-3 font-semibold ${recording ? 'bg-rose-500 text-white' : 'bg-slate-800 text-slate-100 hover:bg-slate-700'}`}>
                {recording ? 'Stop recording' : 'Record voice'}
              </button>
              <label className="ml-auto flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={autoVoice} onChange={e => setAutoVoice(e.target.checked)} />
                Auto-run voice
              </label>
            </div>
            <p className="mt-2 text-xs text-slate-500">
              With “Auto-run voice” on, a spoken command is transcribed, planned and executed automatically — no extra click. Typed commands still ask for approval.
            </p>
          </section>

          <section className="rounded-3xl border border-slate-800 bg-slate-900 p-6">
            <h2 className="mb-1 text-xl font-semibold">Compose &amp; Send</h2>
            <p className="mb-4 text-xs text-slate-500">Send any message to anyone, on any channel.</p>

            <div className="mb-3 flex gap-2">
              <button onClick={() => setSendMode('asis')}
                className={`flex-1 rounded-xl px-3 py-2 text-sm font-semibold ${sendMode === 'asis' ? 'bg-cyan-500 text-slate-950' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}>Send message</button>
              <button onClick={() => setSendMode('ai')}
                className={`flex-1 rounded-xl px-3 py-2 text-sm font-semibold ${sendMode === 'ai' ? 'bg-cyan-500 text-slate-950' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}>Write with AI</button>
            </div>

            <div className="mb-3">
              <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
                <span>Recipients (pick one or many)</span>
                <button type="button"
                  onClick={() => {
                    const all = contacts.map(c => c.name)
                    const selected = sendRecipient.split(',').map(s => s.trim()).filter(Boolean)
                    setSendRecipient(selected.length === all.length ? '' : all.join(', '))
                  }}
                  className="text-cyan-400 hover:underline">
                  {sendRecipient.split(',').map(s => s.trim()).filter(Boolean).length === contacts.length && contacts.length > 0 ? 'Clear all' : 'Select all'}
                </button>
              </div>
              <div className="max-h-32 space-y-1 overflow-auto rounded-xl border border-slate-700 bg-slate-950 p-2">
                {contacts.length === 0 && <div className="p-1 text-xs text-slate-500">No contacts yet.</div>}
                {contacts.map(c => {
                  const selected = sendRecipient.split(',').map(s => s.trim()).filter(Boolean)
                  const isOn = selected.includes(c.name)
                  return (
                    <label key={c.email} className="flex items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-slate-800">
                      <input type="checkbox" checked={isOn}
                        onChange={() => {
                          const next = isOn ? selected.filter(n => n !== c.name) : [...selected, c.name]
                          setSendRecipient(next.join(', '))
                        }} />
                      <span>{c.name}</span>
                      <span className="ml-auto text-xs text-slate-500">{c.email ? '✉' : ''}{c.phone ? ' ☎' : ''}</span>
                    </label>
                  )
                })}
              </div>
              <input value={extraRecipient} onChange={e => setExtraRecipient(e.target.value)}
                placeholder="…or email anyone not listed: name@company.com"
                className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 p-2 text-sm outline-none focus:ring-2 focus:ring-cyan-400" />
              <p className="mt-1 text-xs text-slate-500">Type an email (or “Name &lt;email&gt;”) to reach someone not in contacts — they’re saved automatically.</p>
            </div>

            {sendMode === 'ai' && (
              <input value={sendDocType} onChange={e => setSendDocType(e.target.value)}
                placeholder="Document type, e.g. leave letter"
                className="mb-3 w-full rounded-xl border border-slate-700 bg-slate-950 p-3 text-sm outline-none focus:ring-2 focus:ring-cyan-400" />
            )}

            <textarea value={sendText} onChange={e => setSendText(e.target.value)}
              placeholder={sendMode === 'ai' ? 'What should the AI write? e.g. 2 days medical leave next week' : 'Type your message, e.g. Hi!'}
              className="min-h-24 w-full resize-y rounded-xl border border-slate-700 bg-slate-950 p-3 text-sm outline-none focus:ring-2 focus:ring-cyan-400" />

            <div className="mt-3 flex flex-wrap gap-3 text-sm text-slate-300">
              {(['email', 'whatsapp', 'teams', 'slack'] as const).map(ch => (
                <label key={ch} className="flex items-center gap-2 capitalize">
                  <input type="checkbox" checked={sendChannels[ch]}
                    onChange={e => setSendChannels({ ...sendChannels, [ch]: e.target.checked })} />
                  {ch}
                </label>
              ))}
            </div>

            {!pending && (
              <button disabled={busy} onClick={() => void doSend()}
                className="mt-4 w-full rounded-xl bg-cyan-500 px-5 py-3 font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-50">
                Review &amp; send
              </button>
            )}

            {pending && (
              <div className="mt-4 rounded-xl border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
                <b className="text-amber-300">Confirm — this will be sent to:</b>
                <div className="mt-2 space-y-1">
                  {pending.preview.length === 0 && <div className="text-rose-300">No valid recipient found. Cancel and fix the name/email.</div>}
                  {pending.preview.map((r, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-slate-200">{r.name || '—'}</span>
                      <span className="text-slate-400">{r.email || (r.phone ? r.phone : 'no email/phone')}</span>
                      {!r.email && !r.phone && <Badge text="no destination" tone="amber" />}
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-xs text-slate-500">Check the address is the right person (names can collide). Channels: {pending.channels}.</p>
                <div className="mt-3 flex gap-2">
                  <button disabled={busy} onClick={() => void confirmSend()}
                    className="flex-1 rounded-xl bg-emerald-500 px-4 py-2 font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50">
                    {pending.mode === 'ai' ? 'Confirm: generate & send' : 'Confirm & send'}
                  </button>
                  <button disabled={busy} onClick={() => { setPending(null); setStatus('Cancelled.') }}
                    className="rounded-xl bg-slate-700 px-4 py-2 font-semibold text-slate-100 hover:bg-slate-600">Cancel</button>
                </div>
              </div>
            )}

            {sendResult?.channels && sendResult.channels.length > 0 && (
              <div className="mt-4 space-y-2 rounded-xl border border-slate-800 bg-slate-950 p-3 text-sm">
                {sendResult.channels.map(c => (
                  <div key={c.channel} className="flex flex-wrap items-center gap-2">
                    <span className="w-20 font-semibold capitalize text-slate-300">{c.channel}</span>
                    <Badge text={c.sent ? 'sent' : 'not sent'} tone={c.sent ? 'green' : 'amber'} />
                    {c.reason && <span className="text-xs text-slate-500">{c.reason}</span>}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {data && (
          <section className="mt-6 rounded-3xl border border-slate-800 bg-slate-900 p-6">
            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <h2 className="mb-3 text-xl font-semibold">Transcript</h2>
                <div className="rounded-2xl bg-slate-950 p-4 text-slate-300">{data.transcript}</div>
                <h2 className="mb-3 mt-6 text-xl font-semibold">Execution activity</h2>
                <ol className="space-y-2">
                  {data.activity.map((a, i) => (
                    <li key={i} className="rounded-xl border border-slate-800 bg-slate-950 p-3 text-sm text-slate-400">
                      <span className="mr-2 text-cyan-400">{String(i + 1).padStart(2, '0')}</span>{a}
                    </li>
                  ))}
                </ol>
              </div>
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-xl font-semibold">Action plan</h2>
                  <Badge text={`${data.plan.risk} risk`} tone={data.plan.risk === 'high' ? 'red' : 'green'} />
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-950 p-5">
                  <div className="text-lg font-semibold text-cyan-300">{data.plan.action}</div>
                  <p className="mt-2 text-slate-400">{data.plan.summary}</p>
                  <pre className="mt-4 overflow-auto rounded-xl bg-black/30 p-4 text-xs text-slate-300">{JSON.stringify(data.plan.parameters, null, 2)}</pre>
                  {data.execution_id && (
                    <div className="mt-5 flex gap-3">
                      <button disabled={busy} onClick={() => void confirm(true)}
                        className="rounded-xl bg-emerald-500 px-4 py-3 font-semibold text-slate-950">Approve &amp; execute</button>
                      <button disabled={busy} onClick={() => void confirm(false)}
                        className="rounded-xl bg-rose-500/15 px-4 py-3 font-semibold text-rose-300">Reject</button>
                    </div>
                  )}
                </div>
                {email && (
                  <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950 p-4 text-sm">
                    <div className="mb-1 flex items-center gap-2">
                      <b className="text-slate-200">Email notification</b>
                      <Badge text={email.sent ? 'sent' : 'not sent'} tone={email.sent ? 'green' : 'amber'} />
                    </div>
                    {email.to && <div className="text-slate-400">To: {email.to}</div>}
                    {email.reason && <div className="text-slate-500">{email.reason}</div>}
                  </div>
                )}
                {composedDoc && (
                  <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950 p-4 text-sm">
                    <b className="text-slate-200">Generated document</b>
                    <div className="mt-1 text-slate-400">{composedDoc.title}{composedDoc.recipient ? ` — for ${composedDoc.recipient}` : ''}</div>
                    {composedDoc.body_text && (
                      <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-900 p-3 text-xs text-slate-300">{composedDoc.body_text}</pre>
                    )}
                  </div>
                )}
                {delivery?.channels && delivery.channels.length > 0 && (
                  <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950 p-4 text-sm">
                    <b className="text-slate-200">Delivery across channels</b>
                    <div className="mt-2 space-y-2">
                      {delivery.channels.map((c) => (
                        <div key={c.channel} className="flex flex-wrap items-center gap-2">
                          <span className="w-20 font-semibold capitalize text-slate-300">{c.channel}</span>
                          <Badge text={c.sent ? 'sent' : 'not sent'} tone={c.sent ? 'green' : 'amber'} />
                          {c.to && <span className="text-slate-400">{c.to}</span>}
                          {c.reason && <span className="text-slate-500">{c.reason}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
            {data.result && (
              <div className="mt-6">
                <h2 className="mb-3 text-xl font-semibold">Result</h2>
                <pre className="overflow-auto rounded-2xl border border-slate-800 bg-slate-950 p-5 text-sm text-slate-300">{JSON.stringify(data.result, null, 2)}</pre>
              </div>
            )}
          </section>
        )}

        <section className="mt-6 rounded-3xl border border-slate-800 bg-slate-900 p-6">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex gap-2">
              {(['tasks', 'contacts', 'tickets'] as const).map(t => (
                <button key={t} onClick={() => setTab(t)}
                  className={`rounded-xl px-4 py-2 text-sm font-semibold capitalize ${tab === t ? 'bg-cyan-500 text-slate-950' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}>{t}</button>
              ))}
            </div>
            <button onClick={() => void refreshData()} className="rounded-xl bg-slate-800 px-4 py-2 text-sm text-slate-300 hover:bg-slate-700">Refresh</button>
          </div>

          {tab === 'tasks' && (
            <div className="overflow-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-slate-500"><tr>
                  <th className="p-2">ID</th><th className="p-2">Title</th><th className="p-2">Assignee</th>
                  <th className="p-2">Email</th><th className="p-2">Due</th><th className="p-2">Priority</th><th className="p-2">Notified</th>
                </tr></thead>
                <tbody>
                  {tasks.length === 0 && <tr><td className="p-2 text-slate-500" colSpan={7}>No tasks yet. Create one above.</td></tr>}
                  {tasks.map(t => (
                    <tr key={t.id} className="border-t border-slate-800">
                      <td className="p-2 font-mono text-xs text-cyan-300">{t.id}</td>
                      <td className="p-2">{t.title}</td>
                      <td className="p-2">{t.assignee}</td>
                      <td className="p-2 text-slate-400">{t.assignee_email ?? '—'}</td>
                      <td className="p-2 text-slate-400">{t.due_date}</td>
                      <td className="p-2"><Badge text={t.priority} tone={t.priority === 'high' ? 'red' : 'slate'} /></td>
                      <td className="p-2"><Badge text={t.notified ? 'yes' : 'no'} tone={t.notified ? 'green' : 'amber'} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {tab === 'contacts' && (
            <div>
              <div className="mb-4 flex flex-wrap items-end gap-2">
                <input placeholder="Name" value={newContact.name} onChange={e => setNewContact({ ...newContact, name: e.target.value })}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                <input placeholder="Email" value={newContact.email} onChange={e => setNewContact({ ...newContact, email: e.target.value })}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                <input placeholder="Team" value={newContact.team} onChange={e => setNewContact({ ...newContact, team: e.target.value })}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                <input placeholder="Phone +91…" value={newContact.phone} onChange={e => setNewContact({ ...newContact, phone: e.target.value })}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                <button disabled={busy} onClick={() => void addContact()}
                  className="rounded-xl bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-400">Add contact</button>
              </div>
              <div className="overflow-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-slate-500"><tr><th className="p-2">Name</th><th className="p-2">Team</th><th className="p-2">Email</th><th className="p-2">Phone</th></tr></thead>
                  <tbody>
                    {contacts.map(c => (
                      <tr key={c.email} className="border-t border-slate-800">
                        <td className="p-2">{c.name}</td><td className="p-2 text-slate-400">{c.team}</td>
                        <td className="p-2 text-slate-400">{c.email}</td>
                        <td className="p-2 text-slate-400">{c.phone ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-3 text-xs text-slate-500">Email routes to their address; phone (E.164, e.g. +91…) routes WhatsApp. Add both to reach someone on either channel.</p>
            </div>
          )}

          {tab === 'tickets' && (
            <div className="overflow-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-slate-500"><tr>
                  <th className="p-2">ID</th><th className="p-2">Title</th><th className="p-2">Severity</th><th className="p-2">Status</th><th className="p-2">Owner</th>
                </tr></thead>
                <tbody>
                  {tickets.map(t => (
                    <tr key={t.id} className="border-t border-slate-800">
                      <td className="p-2 font-mono text-xs text-cyan-300">{t.id}</td>
                      <td className="p-2">{t.title}</td>
                      <td className="p-2"><Badge text={t.severity} tone={sevTone(t.severity)} /></td>
                      <td className="p-2 text-slate-400">{t.status}</td>
                      <td className="p-2 text-slate-400">{t.owner}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
