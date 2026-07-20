import { FormEvent, useEffect, useMemo, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import {
  BellRing,
  Check,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Clock3,
  Edit3,
  Inbox,
  LoaderCircle,
  LogOut,
  Mail,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { supabase } from "./supabase";

type WatchStatus = "pending" | "processing" | "active" | "failed";

type WatchIntent = {
  event_type?: string;
  entities?: Array<{ type: string; value: string; required: boolean }>;
  exact_terms?: string[];
  semantic_terms?: string[];
  time_scope?: string | null;
  ambiguities?: string[];
};

type WatchRequest = {
  id: string;
  request: string;
  delivery_email: string;
  enabled: boolean;
  status: WatchStatus;
  revision: number;
  compiled_intent_json: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
};

type FeedEvent = {
  event_id: string;
  title: string;
  source_name?: string;
  seen_at?: string;
};

type Filter = "all" | "active" | "paused";

const demoWatches: WatchRequest[] = [
  {
    id: "demo-1",
    request: "2026 여름계절수업에서 데이터베이스 001분반이 폐강되면 알려줘",
    delivery_email: "student@pusan.ac.kr",
    enabled: true,
    status: "active",
    revision: 1,
    compiled_intent_json: JSON.stringify({
      event_type: "course_cancelled",
      entities: [
        { type: "course", value: "데이터베이스", required: true },
        { type: "section", value: "001", required: true },
      ],
      exact_terms: ["데이터베이스", "001분반", "폐강"],
      time_scope: "2026 여름계절수업",
    }),
    last_error: null,
    created_at: "2026-07-20T13:24:00+09:00",
    updated_at: "2026-07-20T13:25:00+09:00",
    processed_at: "2026-07-20T13:25:00+09:00",
  },
  {
    id: "demo-2",
    request: "2학기 국가장학금 2차 신청 공지가 올라오면 알려줘",
    delivery_email: "student@pusan.ac.kr",
    enabled: true,
    status: "processing",
    revision: 1,
    compiled_intent_json: null,
    last_error: null,
    created_at: "2026-07-20T15:46:00+09:00",
    updated_at: "2026-07-20T15:46:00+09:00",
    processed_at: null,
  },
  {
    id: "demo-3",
    request: "교환학생 추가 모집 공지가 나오면 알려줘",
    delivery_email: "student@pusan.ac.kr",
    enabled: false,
    status: "active",
    revision: 2,
    compiled_intent_json: JSON.stringify({
      event_type: "announcement",
      entities: [{ type: "program", value: "교환학생", required: true }],
      exact_terms: ["교환학생", "추가 모집"],
    }),
    last_error: null,
    created_at: "2026-07-12T10:20:00+09:00",
    updated_at: "2026-07-18T18:11:00+09:00",
    processed_at: "2026-07-12T10:21:00+09:00",
  },
];

const demoMode = import.meta.env.VITE_DEMO_MODE === "true";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState(!demoMode);

  useEffect(() => {
    if (demoMode) return;
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthLoading(false);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setAuthLoading(false);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  if (authLoading) return <LoadingScreen />;
  if (!session && !demoMode) return <SignIn />;
  return <Dashboard session={session} />;
}

function LoadingScreen() {
  return (
    <main className="loading-screen" aria-label="로딩 중">
      <Brand />
      <LoaderCircle className="spin" size={24} />
    </main>
  );
}

function SignIn() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [feed, setFeed] = useState<FeedEvent[]>([]);

  useEffect(() => {
    fetch("https://lofidonut3.github.io/pnu-public-notice-feed/events.json")
      .then((response) => response.json())
      .then((payload) => setFeed((payload.events ?? []).slice(-4).reverse()))
      .catch(() => setFeed([]));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    const redirectTo = `${window.location.origin}${import.meta.env.BASE_URL}`;
    const { error: authError } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: { emailRedirectTo: redirectTo },
    });
    setSubmitting(false);
    if (authError) {
      setError(authError.message);
      return;
    }
    setSent(true);
  }

  return (
    <main className="auth-shell">
      <section className="auth-panel">
        <div className="auth-inner">
          <Brand />
          <div className="auth-copy">
            <h1>원하는 조건의 공지만 이메일로.</h1>
            <p>부산대학교 공개 공지를 기준으로 감시합니다.</p>
          </div>
          {sent ? (
            <div className="mail-sent" role="status">
              <span className="icon-well success"><Mail size={22} /></span>
              <div>
                <strong>로그인 링크를 보냈습니다</strong>
                <p>{email}</p>
              </div>
              <button className="text-button" onClick={() => setSent(false)}>다른 이메일</button>
            </div>
          ) : (
            <form className="auth-form" onSubmit={submit}>
              <label htmlFor="email">이메일</label>
              <div className="field-with-icon">
                <Mail size={18} aria-hidden="true" />
                <input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="student@pusan.ac.kr"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </div>
              {error && <p className="form-error"><CircleAlert size={15} />{error}</p>}
              <button className="primary-button full" disabled={submitting}>
                {submitting ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}
                로그인 링크 보내기
              </button>
            </form>
          )}
        </div>
      </section>
      <section className="feed-panel" aria-label="최근 부산대 공지">
        <div className="feed-panel-inner">
          <div className="feed-heading">
            <span className="live-dot" />
            <span>최근 수집 공지</span>
          </div>
          <div className="feed-stack">
            {(feed.length ? feed : fallbackFeed).map((item, index) => (
              <article className="feed-item" key={item.event_id}>
                <span className="feed-index">{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <h2>{item.title}</h2>
                  <p>{item.source_name ?? "부산대학교"}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}

function Dashboard({ session }: { session: Session | null }) {
  const userEmail = session?.user.email ?? "student@pusan.ac.kr";
  const [watches, setWatches] = useState<WatchRequest[]>(demoMode ? demoWatches : []);
  const [loading, setLoading] = useState(!demoMode);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [toast, setToast] = useState("");
  const [editing, setEditing] = useState<WatchRequest | null>(null);

  async function loadWatches(quiet = false) {
    if (demoMode) return;
    quiet ? setRefreshing(true) : setLoading(true);
    const { data, error } = await supabase
      .from("watch_requests")
      .select("id,request,delivery_email,enabled,status,revision,compiled_intent_json,last_error,created_at,updated_at,processed_at")
      .order("updated_at", { ascending: false });
    if (error) setToast(error.message);
    else setWatches((data ?? []) as WatchRequest[]);
    setLoading(false);
    setRefreshing(false);
  }

  useEffect(() => {
    loadWatches();
    if (demoMode) return;
    const timer = window.setInterval(() => loadWatches(true), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  const visibleWatches = useMemo(() => {
    if (filter === "active") return watches.filter((watch) => watch.enabled);
    if (filter === "paused") return watches.filter((watch) => !watch.enabled);
    return watches;
  }, [filter, watches]);

  const activeCount = watches.filter((watch) => watch.enabled && watch.status === "active").length;
  const waitingCount = watches.filter((watch) => ["pending", "processing"].includes(watch.status)).length;
  const pausedCount = watches.filter((watch) => !watch.enabled).length;

  async function createWatch(request: string, deliveryEmail: string) {
    if (demoMode) {
      const next: WatchRequest = {
        id: `demo-${Date.now()}`,
        request,
        delivery_email: deliveryEmail,
        enabled: true,
        status: "pending",
        revision: 1,
        compiled_intent_json: null,
        last_error: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        processed_at: null,
      };
      setWatches((current) => [next, ...current]);
      setToast("감시 요청을 등록했습니다.");
      return;
    }
    const { error } = await supabase.from("watch_requests").insert({
      request,
      delivery_email: deliveryEmail,
      enabled: true,
    });
    if (error) throw error;
    setToast("감시 요청을 등록했습니다.");
    await loadWatches(true);
  }

  async function toggleWatch(watch: WatchRequest) {
    const enabled = !watch.enabled;
    if (!demoMode) {
      const { error } = await supabase
        .from("watch_requests")
        .update({ enabled })
        .eq("id", watch.id);
      if (error) {
        setToast(error.message);
        return;
      }
    }
    setWatches((current) => current.map((item) => item.id === watch.id ? { ...item, enabled } : item));
    setToast(enabled ? "감시를 다시 시작했습니다." : "감시를 일시정지했습니다.");
  }

  async function saveEdit(watch: WatchRequest, request: string, deliveryEmail: string) {
    if (!demoMode) {
      const { error } = await supabase
        .from("watch_requests")
        .update({ request, delivery_email: deliveryEmail })
        .eq("id", watch.id);
      if (error) throw error;
    }
    setWatches((current) => current.map((item) => item.id === watch.id ? {
      ...item,
      request,
      delivery_email: deliveryEmail,
      status: "pending",
      revision: item.revision + 1,
      compiled_intent_json: null,
    } : item));
    setEditing(null);
    setToast("수정한 조건을 다시 분석합니다.");
    if (!demoMode) await loadWatches(true);
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="header-inner">
          <Brand compact />
          <div className="account-area">
            <span className="account-email">{userEmail}</span>
            {!demoMode && (
              <button className="icon-button" title="로그아웃" onClick={() => supabase.auth.signOut()}>
                <LogOut size={18} />
              </button>
            )}
          </div>
        </div>
      </header>

      <main>
        <section className="overview-band">
          <div className="content-width overview-grid">
            <div className="page-heading">
              <p className="eyebrow">내 공지 감시</p>
              <h1>놓치면 안 되는 조건</h1>
            </div>
            <div className="metrics" aria-label="감시 상태 요약">
              <Metric icon={<BellRing size={18} />} label="감시 중" value={activeCount} tone="green" />
              <Metric icon={<Clock3 size={18} />} label="분석 중" value={waitingCount} tone="amber" />
              <Metric icon={<Pause size={18} />} label="일시정지" value={pausedCount} tone="neutral" />
            </div>
          </div>
        </section>

        <section className="composer-band">
          <div className="content-width">
            <WatchComposer defaultEmail={userEmail} onCreate={createWatch} />
          </div>
        </section>

        <section className="watch-list-band">
          <div className="content-width">
            <div className="list-toolbar">
              <div className="segmented" aria-label="감시 목록 필터">
                <FilterButton active={filter === "all"} onClick={() => setFilter("all")}>전체 {watches.length}</FilterButton>
                <FilterButton active={filter === "active"} onClick={() => setFilter("active")}>활성 {watches.length - pausedCount}</FilterButton>
                <FilterButton active={filter === "paused"} onClick={() => setFilter("paused")}>정지 {pausedCount}</FilterButton>
              </div>
              <button className="icon-button" title="새로고침" onClick={() => loadWatches(true)} disabled={refreshing}>
                <RefreshCw className={refreshing ? "spin" : ""} size={18} />
              </button>
            </div>

            {loading ? (
              <div className="list-loading"><LoaderCircle className="spin" size={22} /> 목록을 불러오는 중</div>
            ) : visibleWatches.length ? (
              <div className="watch-list">
                {visibleWatches.map((watch) => (
                  <WatchRow
                    key={watch.id}
                    watch={watch}
                    onToggle={() => toggleWatch(watch)}
                    onEdit={() => setEditing(watch)}
                  />
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <span className="icon-well"><Inbox size={22} /></span>
                <strong>등록된 감시가 없습니다</strong>
              </div>
            )}
          </div>
        </section>
      </main>

      {editing && (
        <EditDialog watch={editing} onClose={() => setEditing(null)} onSave={saveEdit} />
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "compact" : ""}`}>
      <span className="brand-mark"><BellRing size={compact ? 19 : 22} strokeWidth={2.2} /></span>
      <span>PNU Watch</span>
    </div>
  );
}

function Metric({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: number; tone: string }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{icon}</span>
      <div><strong>{value}</strong><small>{label}</small></div>
    </div>
  );
}

function WatchComposer({ defaultEmail, onCreate }: { defaultEmail: string; onCreate: (request: string, email: string) => Promise<void> }) {
  const [request, setRequest] = useState("");
  const [email, setEmail] = useState(defaultEmail);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (request.trim().length < 5) return;
    setSubmitting(true);
    setError("");
    try {
      await onCreate(request.trim(), email.trim());
      setRequest("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "요청을 등록하지 못했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="composer-tool" onSubmit={submit}>
      <div className="composer-title">
        <span className="icon-well accent"><Sparkles size={20} /></span>
        <div><h2>새 감시 만들기</h2><p>자연어로 입력하세요</p></div>
      </div>
      <label className="sr-only" htmlFor="watch-request">감시 요청</label>
      <textarea
        id="watch-request"
        value={request}
        maxLength={1000}
        onChange={(event) => setRequest(event.target.value)}
        placeholder="예: 2026 여름계절수업에서 데이터베이스 001분반이 폐강되면 알려줘"
        required
      />
      <div className="composer-footer">
        <label className="inline-email">
          <Mail size={16} />
          <span className="sr-only">알림 이메일</span>
          <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        </label>
        <div className="composer-actions">
          <span className="char-count">{request.length}/1000</span>
          <button className="primary-button" disabled={submitting || request.trim().length < 5}>
            {submitting ? <LoaderCircle className="spin" size={18} /> : <Plus size={18} />}
            등록
          </button>
        </div>
      </div>
      {error && <p className="form-error"><CircleAlert size={15} />{error}</p>}
    </form>
  );
}

function WatchRow({ watch, onToggle, onEdit }: { watch: WatchRequest; onToggle: () => void; onEdit: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const intent = parseIntent(watch.compiled_intent_json);
  const status = statusMeta(watch);

  return (
    <article className={`watch-row ${!watch.enabled ? "paused" : ""}`}>
      <div className="watch-main">
        <div className={`status-rail ${status.tone}`} />
        <div className="watch-copy">
          <div className="watch-meta">
            <span className={`status-badge ${status.tone}`}>{status.icon}{status.label}</span>
            <span>v{watch.revision}</span>
            <span>{formatDate(watch.updated_at)}</span>
          </div>
          <h2>{watch.request}</h2>
          <div className="delivery-line"><Mail size={15} />{watch.delivery_email}</div>
          {watch.last_error && <p className="row-error"><CircleAlert size={15} />요청을 다시 수정해 주세요.</p>}
        </div>
        <div className="row-actions">
          <button className="icon-button" title="수정" onClick={onEdit}><Edit3 size={17} /></button>
          <button className="icon-button" title={watch.enabled ? "일시정지" : "다시 시작"} onClick={onToggle}>
            {watch.enabled ? <Pause size={17} /> : <Play size={17} />}
          </button>
          {intent && (
            <button className="icon-button" title="구조화 결과" onClick={() => setExpanded((value) => !value)}>
              {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
            </button>
          )}
        </div>
      </div>
      {expanded && intent && <IntentDetails intent={intent} />}
    </article>
  );
}

function IntentDetails({ intent }: { intent: WatchIntent }) {
  const terms = [...(intent.exact_terms ?? []), ...(intent.semantic_terms ?? [])]
    .filter((term, index, all) => all.indexOf(term) === index)
    .slice(0, 8);
  return (
    <div className="intent-details">
      <div><span>판정 유형</span><strong>{eventTypeLabel(intent.event_type)}</strong></div>
      <div><span>기간</span><strong>{intent.time_scope || "상시"}</strong></div>
      <div className="intent-terms"><span>핵심 조건</span><div>{terms.map((term) => <em key={term}>{term}</em>)}</div></div>
    </div>
  );
}

function EditDialog({ watch, onClose, onSave }: {
  watch: WatchRequest;
  onClose: () => void;
  onSave: (watch: WatchRequest, request: string, email: string) => Promise<void>;
}) {
  const [request, setRequest] = useState(watch.request);
  const [email, setEmail] = useState(watch.delivery_email);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await onSave(watch, request.trim(), email.trim());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "수정하지 못했습니다.");
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="edit-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-header">
          <h2 id="edit-title">감시 조건 수정</h2>
          <button className="icon-button" title="닫기" onClick={onClose}><X size={19} /></button>
        </div>
        <form onSubmit={submit}>
          <label htmlFor="edit-request">감시 요청</label>
          <textarea id="edit-request" value={request} onChange={(event) => setRequest(event.target.value)} maxLength={1000} required />
          <label htmlFor="edit-email">알림 이메일</label>
          <input id="edit-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
          {error && <p className="form-error"><CircleAlert size={15} />{error}</p>}
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>취소</button>
            <button className="primary-button" disabled={saving || request.trim().length < 5}>
              {saving ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}
              저장
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function FilterButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button className={active ? "active" : ""} onClick={onClick}>{children}</button>;
}

function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 4200);
    return () => window.clearTimeout(timer);
  }, [message, onClose]);
  return <div className="toast" role="status"><Check size={17} />{message}<button title="닫기" onClick={onClose}><X size={15} /></button></div>;
}

function parseIntent(value: string | null): WatchIntent | null {
  if (!value) return null;
  try {
    return JSON.parse(value) as WatchIntent;
  } catch {
    return null;
  }
}

function statusMeta(watch: WatchRequest) {
  if (!watch.enabled) return { label: "일시정지", tone: "neutral", icon: <Pause size={13} /> };
  if (watch.status === "pending") return { label: "구조화 대기", tone: "amber", icon: <Clock3 size={13} /> };
  if (watch.status === "processing") return { label: "요청 분석 중", tone: "blue", icon: <LoaderCircle className="spin" size={13} /> };
  if (watch.status === "failed") return { label: "확인 필요", tone: "red", icon: <CircleAlert size={13} /> };
  return { label: "감시 중", tone: "green", icon: <Check size={13} /> };
}

function eventTypeLabel(value?: string) {
  const labels: Record<string, string> = {
    announcement: "공지 등록",
    deadline: "마감",
    course_cancelled: "강좌 폐강",
    course_changed: "강좌 변경",
    result: "결과 발표",
    availability: "신청 가능",
    other: "기타",
  };
  return labels[value ?? ""] ?? "공지 판정";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

const fallbackFeed: FeedEvent[] = [
  { event_id: "fallback-1", title: "2026학년도 2학기 학사 안내", source_name: "학사과" },
  { event_id: "fallback-2", title: "대학생활원 추가 모집 안내", source_name: "대학생활원" },
  { event_id: "fallback-3", title: "교내 장학금 신청 일정", source_name: "학생과" },
];
