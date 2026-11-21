import { useEffect, useState } from 'react'

const base = import.meta.env.VITE_API_BASE_URL ?? ''
async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail))
  return value
}
interface Case { code: string; name: string }
interface Run { id: string; case_code: string; original_task: string; question: string; document_text: string; defense: boolean; iteration: number; model: string; response: string; error?: string; duration_ms: number; success: boolean; blocked: boolean; task_violated: boolean | null; judged_by: string | null }
interface Experiment { id: string; status: string; expected_runs: number; error: string | null; runs: Run[]; metrics: { branches: Record<string, { asr: number | null; label: string; judged: number; total: number; attack_runs: number }>; failed_cases: Run[] } }
interface History { id: string; created_at: string; status: string }
interface ApiResult { id: string; metrics: Record<string, { http_200: number; http_429: number; errors: number; p50_ms: number | null; p95_ms: number | null }>; runs: { defense: boolean; index: number; status: number | null; duration_ms: number; error: string | null }[] }

export default function ExperimentPanel({ model, apiOnly = false }: { model: string; apiOnly?: boolean }) {
  const [cases, setCases] = useState<Case[]>([])
  const [selected, setSelected] = useState<string[]>(['TC-05'])
  const [repeat, setRepeat] = useState(3)
  const [experiment, setExperiment] = useState<Experiment | null>(null)
  const [history, setHistory] = useState<History[]>([])
  const [apiResult, setApiResult] = useState<ApiResult | null>(null)
  const [count, setCount] = useState(10)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const activeId = experiment && ['queued', 'running'].includes(experiment.status) ? experiment.id : null
  async function action(work: () => Promise<void>) {
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(e instanceof Error ? e.message : 'Có lỗi xảy ra') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (apiOnly) return
    let active = true
    Promise.all([call<Case[]>('/api/labs/prompt_injection/cases'), call<History[]>('/api/experiments')])
      .then(([items, saved]) => { if (active) { setCases(items); setHistory(saved) } })
      .catch(e => { if (active) setError(String(e)) })
    return () => { active = false }
  }, [apiOnly])
  useEffect(() => {
    if (!activeId) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const value = await call<Experiment>('/api/experiments/' + activeId)
        if (active) { setExperiment(value); timer = setTimeout(() => void poll(), 1500) }
      } catch (e) { if (active) setError(String(e)) }
    }
    timer = setTimeout(() => void poll(), 1500)
    return () => { active = false; clearTimeout(timer) }
  }, [activeId])
  return <article className="sl-panel">
    <h2>{apiOnly ? 'Đo API bằng service backend (HTTP thật)' : 'Experiment hai nhánh · chấm nhiệm vụ · ASR'}</h2>
    {error && <p role="alert" className="sl-error">{error}</p>}
    {apiOnly ? <>
      <p>Backend dùng httpx gọi /sandbox/echo trên localhost. Hạn mức mặc định 5 request/30 giây; các lượt chạy dùng chung bộ đếm. Chờ hết cửa sổ trước khi đo lại.</p>
      <label>Request mỗi nhánh<input type="number" min={1} max={100} value={count} onChange={e => setCount(Number(e.target.value))} /></label>
      <button disabled={busy} onClick={() => void action(async () => { setApiResult(await call<ApiResult>('/api/labs/api_rate_limit/experiments', 'POST', { requests_per_branch: count })) })}>{busy ? 'Đang đo…' : 'Đo baseline và phòng thủ'}</button>
      {apiResult && <><div className="sl-table-wrap"><table><thead><tr><th>Nhánh</th><th>200</th><th>429</th><th>Lỗi</th><th>p50 ms</th><th>p95 ms</th></tr></thead><tbody>{Object.entries(apiResult.metrics).map(([key, metric]) => <tr key={key}><td>{key === 'true' ? 'Phòng thủ' : 'Baseline'}</td><td>{metric.http_200}</td><td>{metric.http_429}</td><td>{metric.errors}</td><td>{metric.p50_ms ?? '—'}</td><td>{metric.p95_ms ?? '—'}</td></tr>)}</tbody></table></div><p>p50/p95 dùng độ trễ các phản hồi HTTP nhận được, gồm cả 429; lỗi mạng được đếm riêng.</p><button onClick={() => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(apiResult, null, 2)], { type: 'application/json' })); a.download = 'api-experiment.json'; a.click(); URL.revokeObjectURL(a.href) }}>Tải kết quả API JSON</button></>}
    </> : <>
      <p>Mỗi ca chạy cùng model, tài liệu và số lần lặp ở cả hai nhánh. Không phát hiện rò rỉ chưa đủ chứng minh đúng nhiệm vụ: hãy đọc và chấm các lượt chưa đánh giá.</p>
      <label>Ca kiểm thử<select multiple value={selected} onChange={e => setSelected(Array.from(e.target.selectedOptions, o => o.value))}>{cases.map(c => <option key={c.code} value={c.code}>{c.code} · {c.name}</option>)}</select></label>
      <div className="sl-actions"><button onClick={() => setSelected(cases.map(c => c.code))}>Chọn tất cả</button><label>Số lần lặp<input type="number" min={1} max={20} value={repeat} onChange={e => setRepeat(Number(e.target.value))} /></label></div>
      <p>{selected.length * repeat * 2} lượt dự kiến, gồm lượt có thể bị chặn trước khi gọi model. MAX_PI_CALLS được kiểm tra ở backend.</p>
      <button disabled={busy || !!activeId || !selected.length} onClick={() => void action(async () => { setExperiment(await call<Experiment>('/api/labs/prompt_injection/experiments', 'POST', { case_codes: selected, repeat, defenses: [false, true], model })) })}>Tạo experiment hai nhánh</button>
      <div className="sl-actions"><button disabled={busy} onClick={() => void action(async () => setHistory(await call<History[]>('/api/experiments')))}>Tải lịch sử experiment</button><select defaultValue="" onChange={e => void action(async () => setExperiment(await call<Experiment>('/api/experiments/' + e.target.value)))}><option value="" disabled>Mở experiment đã lưu</option>{history.map(h => <option key={h.id} value={h.id}>{new Date(h.created_at).toLocaleString()} · {h.status}</option>)}</select></div>
      {experiment && <>
        <p role="status">{experiment.status} · {experiment.runs.length}/{experiment.expected_runs} lượt · {experiment.id}</p>{experiment.error && <p className="sl-error">{experiment.error}</p>}
        <a href={base + '/api/experiments/' + experiment.id + '/export'} download>Tải experiment JSON (gồm tài liệu, phản hồi và đánh giá)</a>
        <div className="sl-columns">{Object.entries(experiment.metrics.branches).map(([key, branch]) => <div key={key}><h3>{key === 'true' ? 'Sau bảo vệ' : 'Trước bảo vệ'}</h3><p>Đã chấm {branch.judged}/{branch.total}; lượt tấn công: {branch.attack_runs}</p><strong>ASR: {branch.asr === null ? (branch.label === 'complete' ? 'Không có ca tấn công' : 'Chưa đủ dữ liệu') : (branch.asr * 100).toFixed(1) + '%'}</strong></div>)}</div>
        <p>Ca phòng thủ thất bại: {experiment.metrics.failed_cases.map(r => r.case_code + ' (lần ' + r.iteration + ')').join(', ') || 'Chưa ghi nhận; kiểm tra trạng thái chấm trước khi kết luận.'}</p>
        <div className="sl-table-wrap"><table><thead><tr><th>Ca / lần / model</th><th>Nhiệm vụ và tài liệu</th><th>Phòng thủ</th><th>Phản hồi</th><th>ms</th><th>Sai nhiệm vụ?</th></tr></thead><tbody>{experiment.runs.map(run => <tr key={run.id}><td>{run.case_code} / {run.iteration}<br />{run.model}</td><td><details><summary>Nhiệm vụ, câu hỏi, tài liệu</summary><p>{run.original_task}</p><p>{run.question}</p><pre style={{ whiteSpace: 'pre-wrap' }}>{run.document_text}</pre></details></td><td>{run.defense ? 'Bật' : 'Tắt'}{run.blocked && ' · Chặn đầu vào (không gọi model)'}</td><td>{run.error || run.response}</td><td>{run.duration_ms}</td><td><select disabled={busy || !run.success} value={run.task_violated === null ? 'null' : String(run.task_violated)} onChange={e => { const value = e.target.value; void action(async () => setExperiment(await call<Experiment>(`/api/experiments/${experiment.id}/prompt-runs/${run.id}`, 'PATCH', { task_violated: value === 'null' ? null : value === 'true' }))) }}><option value="null">Chưa chấm</option><option value="true">Có</option><option value="false">Không</option></select><small>{run.judged_by ?? 'Chưa đánh giá'}</small></td></tr>)}</tbody></table></div>
      </>}
    </>}
  </article>
}
