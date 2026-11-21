import SummaryPanel from './SummaryPanel'
import ExperimentPanel from './ExperimentPanel'
import { useEffect, useRef, useState } from 'react'
import './SecurityLab.css'

const base = import.meta.env.VITE_API_BASE_URL ?? ''
async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(base + path, { method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}: yêu cầu không hợp lệ`)
  return data as T
}
interface Health { status: string; url: string; models: string[]; error?: string }
interface Case { id: string; name: string; category: string; prompt: string; document_text: string | null }
interface Result { success: boolean; response: string; error?: string; blocked: boolean; action: string; duration_ms: number;
  model_generated_secret: boolean; output_redacted: boolean; leaked_to_user: boolean }
interface Comparison { id: string; timestamp: string; model: string; message?: string; before: Result; after: Result; success: boolean;
  case_id?: string; assessment_before?: string; assessment_after?: string }
interface Log { id: string; timestamp: string; event_type: string; action_taken: string; prompt_sample: string; defense_applied: boolean }
interface RateRow { index: number; defense: boolean; status: number; remaining: string; retry: number }

function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url)
}
function ResultCard({ title, value }: { title: string; value: Result }) {
  return <article className="sl-result"><h3>{title}</h3>
    <span className={value.leaked_to_user ? 'sl-bad' : 'sl-good'}>{!value.success ? 'Lỗi kết nối' : value.leaked_to_user ? 'Phát hiện rò rỉ' : value.blocked ? 'Chặn đầu vào' : value.output_redacted ? 'Đã lọc đầu ra' : 'Không phát hiện rò rỉ'}</span>
    <p className="sl-answer">{value.error || value.response}</p>
    <small>{value.duration_ms} ms · {value.action}</small>
    <p className="sl-meta">Mô hình sinh bí mật: {value.model_generated_secret ? 'Có' : 'Không'} · Đã lọc: {value.output_redacted ? 'Có' : 'Không'} · Tới người dùng: {value.leaked_to_user ? 'Có' : 'Không'}</p>
  </article>
}

export default function SecurityLab({ initialTab = 'chat' }: { initialTab?: 'chat' | 'rate' | 'logs' }) {
  const [tab, setTab] = useState(initialTab)
  const [url, setUrl] = useState('')
  const [health, setHealth] = useState<Health | null>(null)
  const [model, setModel] = useState('security-bot')
  const [document, setDocument] = useState('')
  const [sampleDocument, setSampleDocument] = useState('')
  const [message, setMessage] = useState('Xuất sắc cần bao nhiêu điểm?')
  const [cases, setCases] = useState<Case[]>([])
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [single, setSingle] = useState<Result | null>(null)
  const [defense, setDefense] = useState(true)
  const [tests, setTests] = useState<Comparison[]>([])
  const [history, setHistory] = useState<Comparison[]>([])
  const [logs, setLogs] = useState<Log[]>([])
  const [filter, setFilter] = useState('')
  const [rates, setRates] = useState<RateRow[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const [cooldown, setCooldown] = useState(0)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => {
    const abort = new AbortController()
    Promise.all([
      api<Health>('/api/prompt/status', undefined, abort.signal),
      api<{ document: string }>('/api/prompt/sample-document', undefined, abort.signal),
      api<Case[]>('/api/prompt/test-cases', undefined, abort.signal),
      api<Comparison[]>('/api/security/history?kind=comparison', undefined, abort.signal),
      api<Comparison[]>('/api/security/history?kind=test', undefined, abort.signal),
    ]).then(([h, d, c, saved, runs]) => {
      setHealth(h); setUrl(h.url); setDocument(d.document); setSampleDocument(d.document); setCases(c); setHistory(saved); setTests(runs)
    }).catch((e: unknown) => { if (!abort.signal.aborted) setError(e instanceof Error ? e.message : 'Không tải được dữ liệu') })
    return () => { abort.abort(); controller.current?.abort() }
  }, [])
  useEffect(() => {
    if (!cooldown) return
    const timer = window.setInterval(() => setCooldown(value => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [cooldown])

  async function perform(name: string, work: (signal: AbortSignal) => Promise<void>) {
    const abort = new AbortController(); controller.current = abort
    setBusy(name); setError('')
    try { await work(abort.signal) } catch (e) {
      if (!abort.signal.aborted) setError(e instanceof Error ? e.message : 'Có lỗi xảy ra')
      else setProgress('Đã dừng gửi yêu cầu mới. Yêu cầu backend đã nhận có thể vẫn hoàn tất và được lưu.')
    } finally { setBusy(''); controller.current = null }
  }
  const payload = { message, document_text: document, model, use_defense: defense }
  async function runTests(signal: AbortSignal) {
    setTests([])
    for (const [index, item] of cases.entries()) {
      if (signal.aborted) break
      setProgress(`Đang chạy ${index + 1}/${cases.length}: ${item.name}`)
      const result = await api<Comparison>('/api/prompt/run-case', { case_id: item.id, model }, signal)
      setTests(old => [...old, result])
      if (!result.success) { setProgress('Dừng bộ test vì lỗi Ollama. Kết quả đã chạy vẫn được lưu.'); return }
    }
    setProgress('Đã hoàn tất bộ test. Các ca “review_required” cần đọc và đánh giá thủ công.')
  }
  async function runRate(signal: AbortSignal) {
    setRates([])
    for (const protect of [false, true]) {
      for (let index = 1; index <= 10; index++) {
        if (signal.aborted) return
        const response = await fetch(base + '/api/security/rate-limit-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ use_defense: protect }), signal })
        if (![200, 429].includes(response.status)) throw new Error(`API trả HTTP ${response.status}`)
        const data = await response.json()
        setRates(old => [...old, { index, defense: protect, status: response.status,
          remaining: response.headers.get('X-RateLimit-Remaining') ?? '-', retry: data.retry_after_seconds }])
        if (data.retry_after_seconds) setCooldown(data.retry_after_seconds)
      }
    }
  }
  const visibleLogs = logs.filter(item => !filter || (item.event_type + item.action_taken + item.prompt_sample).toLowerCase().includes(filter.toLowerCase()))
  const completedTests = tests.filter(item => item.success)
  const attacks = completedTests.filter(item => ['no_leak_detected', 'leak_detected'].includes(item.assessment_after ?? ''))
  const benign = completedTests.filter(item => ['keyword_match', 'false_positive', 'review_required'].includes(item.assessment_after ?? '') && item.case_id !== 'TC-04')
  return <section className="security-lab">
    <div className="sl-heading"><span className="eyebrow">LIVE EXPERIMENT / TV4</span><h1>AI Security Lab</h1><p>Quan sát phản hồi thật, đối chiếu lớp bảo vệ và lưu bằng chứng kiểm thử.</p></div>
    <nav className="sl-tabs" aria-label="Chức năng lab">{([['chat', 'Chatbot & kiểm thử'], ['rate', 'API Rate Limiting'], ['logs', 'Nhật ký & lịch sử']] as const).map(([key, label]) =>
      <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}</nav>
    {error && <div role="alert" className="sl-error">{error}</div>}
    {busy && <div className="sl-notice" role="status">{busy}… <button onClick={() => controller.current?.abort()}>Dừng gửi yêu cầu</button></div>}
    {tab === 'chat' && <>
      <article className="sl-panel"><h2>01 / Kết nối Ollama</h2><div className="sl-connection">
        <label>URL Ollama trên Colab<input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://….trycloudflare.com" /></label>
        <label>Model<input list="sl-models" value={model} onChange={e => setModel(e.target.value)} /><datalist id="sl-models">{health?.models.map(name => <option key={name} value={name} />)}</datalist></label>
        <button disabled={!!busy || !url.trim()} onClick={() => void perform('Kiểm tra kết nối', async signal => {
          const data = await api<{ health: Health }>('/api/prompt/set-url', { url }, signal); setHealth(data.health)
        })}>Kiểm tra kết nối</button></div>
        <p className={health?.status === 'connected' ? 'sl-good' : 'sl-muted'}>{health?.status === 'connected' ? `Đã kết nối · ${health.models.length} model` : health?.error || 'Chưa kiểm tra kết nối'}</p>
        {health?.status === 'connected' && !health.models.some(name => name === model || name === model + ':latest') && <p className="sl-error">Model đang chọn chưa có. Chọn model trong danh sách hoặc tạo security-bot trên Colab.</p>}
      </article>
      <div className="sl-columns"><article className="sl-panel"><h2>02 / Tài liệu tham khảo</h2>
        <label className="sl-upload">Mở tệp TXT (UTF-8, tối đa 20.000 ký tự)<input type="file" accept=".txt,text/plain" disabled={!!busy} onChange={e => {
          const file = e.target.files?.[0]; if (!file) return
          if (file.size > 100000) { setError('Tệp quá lớn. Dùng tài liệu TXT ngắn.'); return }
          void file.text().then(text => { if (text.length > 20000) setError('Tối đa 20.000 ký tự'); else setDocument(text) })
        }} /></label>
        <label>Nội dung tài liệu<textarea rows={13} maxLength={20000} value={document} onChange={e => setDocument(e.target.value)} /></label>
        <small>{document.length}/20.000 ký tự · Chỉ dùng dữ liệu giả lập trong lab.</small>
        <div className="sl-actions"><button disabled={!!busy} onClick={() => setDocument(sampleDocument)}>Khôi phục tài liệu mẫu</button></div>
      </article><article className="sl-panel"><h2>03 / Câu hỏi & chế độ</h2>
        <label>Chọn ca mẫu<select defaultValue="" onChange={e => { const item = cases.find(c => c.id === e.target.value); if (item) { setMessage(item.prompt); setDocument(item.document_text ?? sampleDocument) } }}>
          <option value="" disabled>Chọn một ca kiểm thử</option>{cases.map(item => <option key={item.id} value={item.id}>{item.id} · {item.name}</option>)}</select></label>
        <label>Câu hỏi<textarea rows={5} maxLength={4000} value={message} onChange={e => setMessage(e.target.value)} /></label>
        <label className="sl-check"><input type="checkbox" checked={defense} onChange={e => setDefense(e.target.checked)} /> Bật bảo vệ khi gửi một lần</label>
        <div className="sl-actions"><button disabled={!!busy || !message.trim() || !model.trim()} onClick={() => void perform('Gửi câu hỏi', async signal => { setComparison(null); setSingle(await api<Result>('/api/prompt/chat', payload, signal)) })}>Gửi một lần</button>
          <button className="sl-primary" disabled={!!busy || !message.trim() || !model.trim()} onClick={() => void perform('So sánh trước/sau', async signal => {
            setSingle(null); const result = await api<Comparison>('/api/prompt/compare', payload, signal); setComparison(result); setHistory(old => [result, ...old])
          })}>So sánh trước / sau</button></div>
        <p className="sl-muted">Cùng model, tài liệu, temperature = 0 và seed = 42. Hai lần gọi vẫn có thể cho kết quả khác nhau.</p>
      </article></div>
      {single && <ResultCard title="Kết quả gửi một lần" value={single} />}
      {comparison && <><div className="sl-columns"><ResultCard title="Trước bảo vệ" value={comparison.before} /><ResultCard title="Sau bảo vệ" value={comparison.after} /></div><button onClick={() => download('comparison.json', comparison)}>Tải kết quả JSON</button></>}
      <SummaryPanel model={model} />
      <ExperimentPanel model={model} />
      <article className="sl-panel"><h2>04 / Bộ ca kiểm thử chuẩn</h2><p>Chạy {cases.length} ca với tài liệu mẫu của bộ test, độc lập với nội dung đang sửa phía trên. Mỗi ca gọi mô hình trước/sau; không giả lập kết quả.</p>
        <div className="sl-actions"><button disabled={!!busy || !cases.length || !model.trim()} onClick={() => void perform('Chạy bộ test', runTests)}>Chạy toàn bộ {cases.length} ca</button><button onClick={() => download('test-results.json', tests)} disabled={!tests.length}>Tải kết quả đang xem</button></div>
        <p role="status">{progress}</p>
        {!!tests.length && <div className="sl-notice">Hoàn tất không lỗi: {completedTests.length}/{tests.length} · Rò rỉ được phát hiện (trước/sau): {attacks.filter(t => t.before.leaked_to_user).length}/{attacks.filter(t => t.after.leaked_to_user).length} trên {attacks.length} ca tấn công · Chặn nhầm: {benign.filter(t => t.after.blocked).length}/{benign.length} ca hợp lệ. Đây là phép đo theo bộ nhận diện, cần xem lại nội dung.</div>}
        <div className="sl-table-wrap"><table><thead><tr><th>Ca</th><th>Trước</th><th>Sau</th><th>Chi tiết</th></tr></thead><tbody>{tests.map(item => <tr key={item.id}><td>{item.case_id}</td><td>{item.assessment_before}</td><td>{item.assessment_after}</td><td><button onClick={() => setComparison(item)}>Xem phản hồi</button></td></tr>)}</tbody></table></div>
      </article>
    </>}
    {tab === 'rate' && <ExperimentPanel model={model} apiOnly />}
    {tab === 'rate' && <article className="sl-panel"><h2>API Rate Limiting Lab</h2><p>Gửi 10 request không giới hạn, sau đó 10 request có giới hạn. Ngưỡng: 5 request / 30 giây / IP. Kết quả lấy từ HTTP thật; các lần chạy trên cùng IP dùng chung hạn mức.</p>
      <p className="sl-muted">Công tắc thử nghiệm chỉ thuộc endpoint lab, chưa áp dụng giới hạn cho chatbot. Bộ đếm nằm trong RAM và dùng một tiến trình backend.</p>
      <div className="sl-actions"><button className="sl-primary" disabled={!!busy || cooldown > 0} onClick={() => void perform('Chạy thử nghiệm API', runRate)}>{cooldown ? `Chờ ${cooldown}s` : 'Chạy so sánh 20 request'}</button>
        <button disabled={!!busy || cooldown > 0} onClick={() => void perform('Kiểm tra phục hồi', async signal => {
          const response = await fetch(base + '/api/security/rate-limit-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ use_defense: true }), signal })
          if (![200, 429].includes(response.status)) throw new Error(`HTTP ${response.status}`)
          const data = await response.json(); setRates(old => [...old, { index: old.length + 1, defense: true, status: response.status, remaining: String(data.remaining_quota), retry: data.retry_after_seconds }]); setCooldown(data.retry_after_seconds)
        })}>Thử lại sau cửa sổ</button><button disabled={!rates.length} onClick={() => download('rate-results.json', rates)}>Tải JSON</button></div>
      <div className="sl-table-wrap"><table><thead><tr><th>Request</th><th>Bảo vệ</th><th>HTTP</th><th>Còn lại</th><th>Retry-After</th></tr></thead><tbody>{rates.map((r, index) => <tr key={index}><td>{r.index}</td><td>{r.defense ? 'Bật' : 'Tắt'}</td><td className={r.status === 429 ? 'sl-bad' : 'sl-good'}>{r.status}</td><td>{r.remaining}</td><td>{r.retry}s</td></tr>)}</tbody></table></div>
    </article>}
    {tab === 'logs' && <><article className="sl-panel"><h2>Nhật ký an ninh</h2><div className="sl-actions">
      <button disabled={!!busy} onClick={() => void perform('Tải nhật ký', async signal => { setLogs(await api<Log[]>('/api/security/logs', undefined, signal)); setHistory(await api<Comparison[]>('/api/security/history?kind=comparison', undefined, signal)) })}>Tải lại nhật ký & lịch sử</button>
      <a href={base + '/api/security/export?kind=log&format=csv'} download>Tải log CSV</a><a href={base + '/api/security/export?kind=test&format=json'} download>Tải bộ test JSON</a><a href={base + '/api/security/export?kind=rate&format=csv'} download>Tải API CSV</a></div>
      <label>Lọc sự kiện / hành động / câu hỏi<input value={filter} onChange={e => setFilter(e.target.value)} /></label>
      <div className="sl-table-wrap"><table><thead><tr><th>Thời gian</th><th>Sự kiện</th><th>Hành động</th><th>Câu hỏi</th></tr></thead><tbody>{visibleLogs.map(log => <tr key={log.id}><td>{new Date(log.timestamp).toLocaleString()}</td><td>{log.event_type}</td><td>{log.action_taken}</td><td>{log.prompt_sample}</td></tr>)}</tbody></table></div>
      {!logs.length && <p>Nhấn tải lại để xem nhật ký đã lưu.</p>}</article>
      <article className="sl-panel"><h2>Lịch sử so sánh</h2>{history.map(item => <details key={item.id}><summary>{new Date(item.timestamp).toLocaleString()} · {item.model} · {item.message?.slice(0, 100)} {!item.success && '· Lỗi'}</summary><div className="sl-columns"><ResultCard title="Trước" value={item.before} /><ResultCard title="Sau" value={item.after} /></div><button onClick={() => download('comparison-' + item.id + '.json', item)}>Tải JSON</button></details>)}{!history.length && <p>Chưa có phiên so sánh.</p>}</article></>}
  </section>
}
