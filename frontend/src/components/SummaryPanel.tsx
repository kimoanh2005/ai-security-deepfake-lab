import { useEffect, useState } from 'react'
const base = import.meta.env.VITE_API_BASE_URL ?? ''
interface Case { id: string; name: string; document_text: string }
interface Run { iteration: number; defense: boolean; schema_valid: boolean | null; action: string; authoritative_status: string; simulated_baseline_status: string; raw_response: string; summary_for_user: string | null; duration_ms: number; quality_status?: string; quality_assessment?: { reference_passed: boolean | null; missing_concepts: string[]; reasons: string[]; human_review_required: boolean }; error?: string; content_action?: string; content_reason?: string | null; backend_notice?: string | null; attempts?: { attempt: number; raw_response: string; action: string }[] }
interface Result { id: string; runs: Run[]; document_text: string }
async function request<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(base + path, { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const data = await r.json()
  if (!r.ok) throw new Error(JSON.stringify(data.detail ?? data))
  return data
}
export default function SummaryPanel({ model }: { model: string }) {
  const [cases, setCases] = useState<Case[]>([])
  const [document, setDocument] = useState('')
  const [repeat, setRepeat] = useState(1)
  const [result, setResult] = useState<Result | null>(null)
  const [history, setHistory] = useState<Result[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [raw, setRaw] = useState('{"summary":"Đề xuất phê duyệt từ nội dung tài liệu","approval_requested":true}')
  const [inspection, setInspection] = useState<unknown>(null)
  useEffect(() => {
    let active = true
    request<Case[]>('/api/prompt/summary/cases').then(data => { if (active) { setCases(data); setDocument(data[0]?.document_text ?? '') } }).catch(e => { if (active) setError(String(e)) })
    return () => { active = false }
  }, [])
  async function act(work: () => Promise<void>) {
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  function download() {
    const a = window.document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })); a.download = 'summary-experiment.json'; a.click(); URL.revokeObjectURL(a.href)
  }
  return <article className="sl-panel"><h2>Tóm tắt có schema &amp; kiểm soát phê duyệt</h2>
    <p>AI chỉ đề xuất. Trạng thái chính thức do backend giữ là PENDING (chưa duyệt). Demo không cấp quyền duyệt cho AI và không có thao tác duyệt thật.</p>
    {error && <p className="sl-error" role="alert">{error}</p>}
    <label>Ca thử<select disabled={busy} defaultValue="" onChange={e => setDocument(cases.find(c => c.id === e.target.value)?.document_text ?? '')}><option value="" disabled>Chọn ca</option>{cases.map(c => <option key={c.id} value={c.id}>{c.id} · {c.name}</option>)}</select></label>
    <label>Tài liệu không đáng tin<textarea disabled={busy} rows={5} maxLength={20000} value={document} onChange={e => setDocument(e.target.value)} /></label>
    <label>Số lần lặp<input disabled={busy} type="number" min={1} max={3} value={repeat} onChange={e => setRepeat(Number(e.target.value))} /></label>
    <div className="sl-actions"><button disabled={busy || !document.trim()} onClick={() => void act(async () => setResult(await request<Result>('/api/prompt/summary/compare', { document_text: document, repeat, model })))}>So sánh tóm tắt trước / sau</button><button disabled={busy} onClick={() => void act(async () => setHistory(await request<Result[]>('/api/prompt/summary/history')))}>Tải lịch sử tóm tắt</button></div>
    {busy && <p role="status">Đang xử lý. So sánh gọi Colab hai lần mỗi lần lặp; giữ trang và backend đang chạy.</p>}
    {!!history.length && <select defaultValue="" onChange={e => setResult(history.find(h => h.id === e.target.value) ?? null)}><option value="" disabled>Mở kết quả đã lưu</option>{history.map(h => <option key={h.id} value={h.id}>{h.id}</option>)}</select>}
    {result && <><button onClick={download}>Tải JSON tóm tắt</button><div className="sl-table-wrap"><table><thead><tr><th>Nhánh / lần</th><th>Schema</th><th>Chất lượng nội dung (tham chiếu)</th><th>Quyết định backend</th><th>Trạng thái chính thức</th><th>Phản hồi / ms</th></tr></thead><tbody>{result.runs.map((r, i) => <tr key={i}><td>{r.defense ? 'Phòng thủ' : 'Baseline'} / {r.iteration}</td><td>{r.schema_valid === null ? 'Lỗi upstream' : r.schema_valid ? 'Hợp lệ' : 'Không hợp lệ'}</td><td>{r.quality_status === 'REFERENCE_CHECK_PASSED' ? 'Đủ ý theo quy tắc tham chiếu; cần đọc lại' : r.quality_status === 'CONTENT_REVIEW_REQUIRED' ? 'Cần người đánh giá nội dung' : 'Chưa đánh giá'}{r.quality_assessment && <><p>Ý còn thiếu: {r.quality_assessment.missing_concepts.join(', ') || 'Không ghi nhận'}</p><small>{r.quality_assessment.reasons.join(', ')}</small></>}</td><td>{r.action}{r.defense && <p>{r.content_action ?? 'Kết quả cũ: chưa kiểm tra nội dung'}</p>}{!r.defense && <p>Mô phỏng bên tiêu thụ thiếu bảo vệ: {r.simulated_baseline_status}</p>}</td><td>{r.authoritative_status === 'PENDING' ? 'Chưa duyệt (backend)' : r.authoritative_status}</td><td>{r.error || r.summary_for_user || r.backend_notice}{r.summary_for_user && r.backend_notice && <p>{r.backend_notice}</p>}{r.content_reason && <p>{r.content_reason}</p>}<details><summary>Đầu ra AI thô (chỉ để kiểm tra)</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{r.raw_response}</pre>{r.attempts?.map(a => <div key={a.attempt}><strong>Lần sinh {a.attempt}: {a.action}</strong><pre style={{ whiteSpace: 'pre-wrap' }}>{a.raw_response}</pre></div>)}</details>{r.duration_ms} ms</td></tr>)}</tbody></table></div><p>Schema hợp lệ không chứng minh tóm tắt đúng. Đọc phản hồi để kiểm tra AI có nói sai trạng thái hoặc làm theo tài liệu hay không. Baseline APPROVED chỉ là mô phỏng, không phải hồ sơ thật.</p></>}
    <details><summary>Kiểm tra riêng schema và quyền hành động — không gọi AI</summary><p>JSON thiếu/sai trường → INVALID_OUTPUT. JSON hợp lệ với approval_requested=true → ACTION_DENIED, trạng thái PENDING. Đây là test thủ công backend, không tính là kết quả mô hình thật.</p><textarea rows={4} value={raw} onChange={e => setRaw(e.target.value)} /><button disabled={busy} onClick={() => void act(async () => setInspection(await request('/api/prompt/summary/inspect-output', { raw_response: raw })))}>Kiểm tra JSON và quyền</button>{inspection != null && <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(inspection, null, 2)}</pre>}</details>
  </article>
}
