import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  Clock3,
  FileVideo2,
  FlaskConical,
  History,
  LayoutDashboard,
  LockKeyhole,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  TerminalSquare,
  Video,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { getLabs, type Lab } from '../services/api'

const labPresentation = [
  {
    id: 1,
    shortName: 'Deepfake & Identity',
    description: 'Xác minh video và nhận diện dấu hiệu giả mạo danh tính.',
    detail:
      'Lab này dự kiến nhận video có quyền sử dụng, trích xuất metadata và khung hình để phục vụ quy trình xác minh.',
    icon: Video,
    tone: 'violet',
    capabilities: ['Upload video', 'Trích xuất khung hình', 'Detector deepfake'],
  },
  {
    id: 2,
    shortName: 'Prompt Injection',
    description: 'Khảo sát rủi ro chỉ thị độc hại trong chatbot tài liệu.',
    detail:
      'Lab này dự kiến kết nối Ollama, chạy bộ ca kiểm thử trong sandbox và đối chiếu phản hồi trước/sau lớp bảo vệ.',
    icon: Bot,
    tone: 'blue',
    capabilities: ['Chatbot Ollama', 'Ca kiểm thử sandbox', 'So sánh trước/sau'],
  },
  {
    id: 3,
    shortName: 'API Security',
    description: 'Kiểm tra phản hồi API và cơ chế giới hạn tần suất.',
    detail:
      'Lab này dự kiến gửi request đến API thử nghiệm, ghi nhận mã trạng thái và đánh giá giới hạn tần suất.',
    icon: TerminalSquare,
    tone: 'orange',
    capabilities: ['Gửi request thử nghiệm', 'Quan sát HTTP status', 'Rate limiting & log'],
  },
  {
    id: 4,
    shortName: 'Data Poisoning',
    description: 'Khu vực mở rộng dành cho thử nghiệm nâng cao.',
    detail: 'Lab đang ở trạng thái lên kế hoạch, chưa có quy trình kiểm thử được tích hợp.',
    icon: FlaskConical,
    tone: 'green',
    capabilities: ['Chưa có nội dung thử nghiệm'],
  },
]

function getLabPresentation(lab: Lab) {
  return (
    labPresentation.find((item) => item.id === lab.id) ?? {
      id: lab.id,
      shortName: lab.name,
      description: 'Mô-đun thử nghiệm bảo mật trong môi trường lab.',
      detail: 'Mô-đun này chưa có hướng dẫn chi tiết ở giao diện.',
      icon: Shield,
      tone: 'blue',
      capabilities: ['Chưa khai báo chức năng'],
    }
  )
}

function statusLabel(status: string) {
  if (status === 'active') return 'Đã khai báo'
  if (status === 'planned') return 'Đang lên kế hoạch'
  return status
}

interface DashboardProps {
  labs: Lab[]
  selectedLabId: number | null
  onSelectLab: (id: number) => void
  onBrowseLabs: () => void
}

function Overview({ labs, selectedLabId, onSelectLab, onBrowseLabs }: DashboardProps) {
  const activeLabs = labs.filter((lab) => lab.status === 'active').length

  return (
    <>
      <section className="welcome-panel">
        <div className="welcome-copy">
          <div className="eyebrow"><span className="live-dot" /> KHÔNG GIAN KIỂM THỬ CÓ KIỂM SOÁT</div>
          <h1>Bảo mật bắt đầu từ<br /><span>những câu hỏi đúng.</span></h1>
          <p>
            Môi trường học tập để quan sát rủi ro AI và an ninh ứng dụng.
            Thực hiện thử nghiệm trong phạm vi được cho phép.
          </p>
          <button className="button button-primary" onClick={onBrowseLabs}>
            Khám phá các Lab <ArrowRight size={16} />
          </button>
        </div>
        <div className="welcome-art" aria-hidden="true">
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <div className="orbit-core"><ShieldCheck size={52} strokeWidth={1.4} /></div>
          <span className="orbit-node node-one"><Bot size={17} /></span>
          <span className="orbit-node node-two"><LockKeyhole size={17} /></span>
          <span className="orbit-node node-three"><Activity size={17} /></span>
        </div>
        <div className="welcome-index">01 <span>/ 04</span></div>
      </section>

      <section className="stats-grid" aria-label="Tổng quan hệ thống">
        <article className="stat-card">
          <span className="stat-icon violet"><FlaskConical size={18} /></span>
          <div><strong>{labs.length}</strong><span>Lab được backend khai báo</span></div>
          <span className="stat-foot">Từ API</span>
        </article>
        <article className="stat-card">
          <span className="stat-icon green"><Check size={18} /></span>
          <div><strong>{activeLabs}</strong><span>Lab có trạng thái active</span></div>
          <span className="stat-foot">Không đồng nghĩa đã hoàn thiện</span>
        </article>
        <article className="stat-card">
          <span className="stat-icon blue"><History size={18} /></span>
          <div><strong>—</strong><span>Phiên kiểm thử đã lưu</span></div>
          <span className="stat-foot">API lịch sử chưa có</span>
        </article>
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div><span className="eyebrow">MÔ-ĐUN THỰC HÀNH</span><h2>Các phòng Lab</h2></div>
          <button className="text-button" onClick={onBrowseLabs}>Xem tất cả <ArrowRight size={15} /></button>
        </div>
        <LabGrid labs={labs.slice(0, 3)} selectedLabId={selectedLabId} onSelectLab={onSelectLab} />
      </section>

      <section className="notice-strip">
        <span className="notice-icon"><ShieldAlert size={18} /></span>
        <p><strong>Thử nghiệm có trách nhiệm.</strong> Chỉ sử dụng dữ liệu được phép và các hệ thống nằm trong phạm vi lab.</p>
        <ArrowDownLeft className="notice-arrow" size={17} />
      </section>
    </>
  )
}

function LabDirectory({
  labs,
  selectedLabId,
  onSelectLab,
}: {
  labs: Lab[]
  selectedLabId: number | null
  onSelectLab: (id: number) => void
}) {
  return (
    <section className="directory-view">
      <div className="section-heading">
        <div><span className="eyebrow">WORKSPACE · PHÒNG THỰC HÀNH</span><h2>Tất cả các Lab</h2></div>
      </div>
      <LabGrid labs={labs} selectedLabId={selectedLabId} onSelectLab={onSelectLab} />
    </section>
  )
}

function LabGrid({
  labs,
  selectedLabId,
  onSelectLab,
}: {
  labs: Lab[]
  selectedLabId: number | null
  onSelectLab: (id: number) => void
}) {
  if (labs.length === 0) {
    return <div className="empty-panel">Backend chưa khai báo Lab nào.</div>
  }

  return (
    <div className="lab-grid">
      {labs.map((lab) => {
        const presentation = getLabPresentation(lab)
        const Icon = presentation.icon
        return (
          <article className="lab-card" key={lab.id}>
            <div className="lab-card-top">
              <span className={`lab-icon ${presentation.tone}`}><Icon size={20} /></span>
              <span className={`status-badge ${lab.status === 'active' ? 'status-active' : 'status-planned'}`}>
                <span />{statusLabel(lab.status)}
              </span>
            </div>
            <h3>{presentation.shortName}</h3>
            <p>{presentation.description}</p>
            <button
              className="lab-link"
              onClick={() => onSelectLab(lab.id)}
              aria-current={selectedLabId === lab.id ? 'page' : undefined}
            >
              Xem thông tin <ChevronRight size={16} />
            </button>
          </article>
        )
      })}
    </div>
  )
}

function LabDetail({ lab }: { lab: Lab }) {
  const presentation = getLabPresentation(lab)
  const Icon = presentation.icon

  return (
    <section className="detail-view">
      <div className="detail-hero">
        <span className={`lab-icon large ${presentation.tone}`}><Icon size={25} /></span>
        <div>
          <span className="eyebrow">LAB {String(lab.id).padStart(2, '0')} · {statusLabel(lab.status)}</span>
          <h1>{presentation.shortName}</h1>
          <p>{presentation.detail}</p>
        </div>
      </div>
      <div className="detail-columns">
        <article className="content-card">
          <div className="card-title"><FileVideo2 size={17} /><h2>Phạm vi chức năng</h2></div>
          <ul className="capability-list">
            {presentation.capabilities.map((capability) => (
              <li key={capability}><span className="list-check"><Check size={13} /></span>{capability}</li>
            ))}
          </ul>
          <div className="integration-callout">
            <ShieldAlert size={18} />
            <p><strong>Chưa có API thực thi Lab.</strong> Backend hiện chỉ cung cấp danh sách Lab; giao diện không giả lập upload, phân tích AI hay lưu kết quả.</p>
          </div>
        </article>
        <article className="content-card">
          <div className="card-title"><Activity size={17} /><h2>Trạng thái tích hợp</h2></div>
          <div className="integration-row"><span>Đăng ký Lab qua API</span><b className="good-state"><Check size={14} /> Đã kết nối</b></div>
          <div className="integration-row"><span>Chức năng kiểm thử</span><b className="waiting-state"><Clock3 size={14} /> Chưa tích hợp</b></div>
          <div className="integration-row"><span>Lịch sử và bằng chứng</span><b className="waiting-state"><Clock3 size={14} /> Chưa có API</b></div>
        </article>
      </div>
    </section>
  )
}

export default function Dashboard() {
  const [labs, setLabs] = useState<Lab[]>([])
  const [selectedLabId, setSelectedLabId] = useState<number | null>(null)
  const [showAllLabs, setShowAllLabs] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadLabs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getLabs()
      setLabs(result)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Không thể kết nối tới backend.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    getLabs()
      .then((result) => {
        if (active) setLabs(result)
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : 'Không thể kết nối tới backend.')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    return () => {
      active = false
    }
  }, [])

  const selectedLab = labs.find((lab) => lab.id === selectedLabId)
  const activePage = selectedLab ? 'lab' : showAllLabs ? 'labs' : 'overview'

  function openDashboard() {
    setShowAllLabs(false)
    setSelectedLabId(null)
  }

  function browseLabs() {
    setShowAllLabs(true)
    setSelectedLabId(null)
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={openDashboard} aria-label="Về trang tổng quan">
          <span className="brand-mark"><Shield size={22} /></span>
          <span className="brand-name">SENTINEL<span>AI SECURITY LAB</span></span>
        </button>

        <div className="nav-caption">WORKSPACE</div>
        <nav className="main-nav" aria-label="Điều hướng chính">
          <button className={`nav-item ${activePage === 'overview' ? 'selected' : ''}`} onClick={openDashboard}>
            <LayoutDashboard size={17} /> Tổng quan <span className="nav-active-mark" />
          </button>
          <div className="nav-caption nav-caption-labs">PHÒNG THỰC HÀNH</div>
          {labs.map((lab) => {
            const presentation = getLabPresentation(lab)
            const Icon = presentation.icon
            return (
              <button
                className={`nav-item ${selectedLabId === lab.id ? 'selected' : ''}`}
                key={lab.id}
                onClick={() => setSelectedLabId(lab.id)}
              >
                <Icon size={17} /><span className="nav-label">{presentation.shortName}</span>
              </button>
            )
          })}
          <button className="nav-item nav-muted" disabled title="Chưa có API lịch sử">
            <History size={17} /> Lịch sử hoạt động <span className="soon-tag">SẮP CÓ</span>
          </button>
        </nav>

        <div className="sidebar-bottom">
          <div className={`connection-card ${error ? 'connection-error' : ''}`}>
            <span className="connection-icon">{error ? <WifiOff size={16} /> : <Wifi size={16} />}</span>
            <span><strong>{error ? 'Backend mất kết nối' : 'Backend API'}</strong><small>{error ? 'Kiểm tra dịch vụ' : 'FastAPI · /api/labs'}</small></span>
            <i className={error ? 'connection-dot offline' : 'connection-dot'} />
          </div>
          <div className="profile-chip"><span className="profile-avatar">S</span><span><strong>Security Lab</strong><small>Không gian học tập</small></span></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14} /><strong>{selectedLab ? getLabPresentation(selectedLab).shortName : showAllLabs ? 'Tất cả các Lab' : 'Tổng quan'}</strong></div>
          <div className="topbar-right">
            <span className="environment-tag"><span /> LOCAL ENVIRONMENT</span>
            <button className="refresh-button" onClick={() => void loadLabs()} disabled={loading} aria-label="Tải lại dữ liệu">
              <RefreshCw size={16} className={loading ? 'spinning' : ''} />
            </button>
          </div>
        </header>

        <div className="page-content">
          {loading && labs.length === 0 ? (
            <div className="state-panel"><span className="loader" /><strong>Đang kết nối backend…</strong><p>Đang tải danh sách Lab từ FastAPI.</p></div>
          ) : error && labs.length === 0 ? (
            <div className="state-panel error-panel">
              <span className="state-icon"><WifiOff size={21} /></span>
              <strong>Chưa kết nối được backend</strong>
              <p>{error} Hãy kiểm tra backend tại <code>http://127.0.0.1:8000</code> rồi thử lại.</p>
              <button className="button button-primary" onClick={() => void loadLabs()}><RefreshCw size={15} /> Thử kết nối lại</button>
            </div>
          ) : (
            <>
              {error && <div className="inline-error" role="alert">{error} <button onClick={() => void loadLabs()}>Thử lại</button></div>}
              {selectedLab ? (
                <LabDetail lab={selectedLab} />
              ) : showAllLabs ? (
                <LabDirectory labs={labs} selectedLabId={selectedLabId} onSelectLab={setSelectedLabId} />
              ) : (
                <Overview labs={labs} selectedLabId={selectedLabId} onSelectLab={setSelectedLabId} onBrowseLabs={browseLabs} />
              )}
            </>
          )}
        </div>
        <footer className="page-footer"><span>© 2026 SENTINEL AI SECURITY LAB</span><span><span className="footer-dot" /> LAB ENVIRONMENT · CHỈ DÙNG CÓ ỦY QUYỀN</span></footer>
      </main>
    </div>
  )
}