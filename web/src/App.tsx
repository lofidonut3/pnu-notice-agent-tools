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
  ExternalLink,
  FileText,
  Inbox,
  ListChecks,
  LoaderCircle,
  LogOut,
  Mail,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
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
type DashboardView = "watches" | "notifications";

type UserNotification = {
  id: string;
  watch_request_id: string;
  classification: "matched" | "uncertain";
  delivery_status: "not_applicable" | "queued" | "retry" | "sent" | "needs_attention";
  title: string;
  summary: string;
  notice_url: string | null;
  facts_json: string;
  evidence_json: string;
  last_error: string | null;
  read_at: string | null;
  created_at: string;
  sent_at: string | null;
};

type ServiceHealth = {
  status: "healthy" | "degraded" | "unhealthy";
  checked_at: string;
  open_incident_count: number;
  summary: string;
};

type AlertFact = { text: string; evidence_ids?: string[] };
type AlertEvidence = { id: string; source_name: string; page?: number | null; row?: number | null };

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

const demoNotifications: UserNotification[] = [
  {
    id: "alert-demo-1",
    watch_request_id: "demo-1",
    classification: "matched",
    delivery_status: "sent",
    title: "2026 여름계절수업 폐강 강좌 안내",
    summary: "데이터베이스 001분반이 폐강 강좌 목록에서 확인되었습니다.",
    notice_url: "https://www.pusan.ac.kr/kor/CMS/Board/Board.do?mCode=MN095",
    facts_json: JSON.stringify([{ text: "데이터베이스 001분반의 상태가 폐강으로 표시되어 있습니다.", evidence_ids: ["E002"] }]),
    evidence_json: JSON.stringify([{ id: "E002", source_name: "2026 여름계절수업 폐강목록.xlsx", row: 42 }]),
    last_error: null,
    read_at: null,
    created_at: "2026-07-21T12:10:00+09:00",
    sent_at: "2026-07-21T12:10:04+09:00",
  },
  {
    id: "alert-demo-2",
    watch_request_id: "demo-2",
    classification: "uncertain",
    delivery_status: "not_applicable",
    title: "2026학년도 2학기 국가장학금 안내",
    summary: "2차 신청 여부를 확정할 수 있는 일정 근거가 부족합니다.",
    notice_url: "https://www.pusan.ac.kr/kor/CMS/Board/Board.do?mCode=MN095",
    facts_json: "[]",
    evidence_json: JSON.stringify([{ id: "E001", source_name: "공지 본문" }]),
    last_error: "analysis remained uncertain",
    read_at: "2026-07-21T12:20:00+09:00",
    created_at: "2026-07-21T11:48:00+09:00",
    sent_at: null,
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
  const [view, setView] = useState<DashboardView>("watches");
  const [notifications, setNotifications] = useState<UserNotification[]>(demoMode ? demoNotifications : []);
  const [health, setHealth] = useState<ServiceHealth | null>(demoMode ? {
    status: "healthy",
    checked_at: "2026-07-21T12:20:00+09:00",
    open_incident_count: 0,
    summary: "공지 감시 서비스가 정상 작동 중입니다.",
  } : null);
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

  async function loadNotifications() {
    if (demoMode) return;
    const [{ data: alertRows, error: alertError }, { data: healthRow, error: healthError }] = await Promise.all([
      supabase
        .from("user_notifications")
        .select("id,watch_request_id,classification,delivery_status,title,summary,notice_url,facts_json,evidence_json,last_error,read_at,created_at,sent_at")
        .order("created_at", { ascending: false })
        .limit(100),
      supabase
        .from("service_health")
        .select("status,checked_at,open_incident_count,summary")
        .eq("id", "runtime")
        .maybeSingle(),
    ]);
    if (alertError) setToast(alertError.message);
    else setNotifications((alertRows ?? []) as UserNotification[]);
    if (healthError) setToast(healthError.message);
    else setHealth((healthRow as ServiceHealth | null) ?? null);
  }

  async function wakeWorker(requestId: string) {
    const { error } = await supabase.functions.invoke("dispatch-watch-request", {
      body: { request_id: requestId },
    });
    return !error;
  }

  useEffect(() => {
    loadWatches();
    loadNotifications();
    if (demoMode) return;
    const timer = window.setInterval(() => {
      loadWatches(true);
      loadNotifications();
    }, 15_000);
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
  const unreadCount = notifications.filter((notification) => !notification.read_at).length;

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
    const { data, error } = await supabase.from("watch_requests").insert({
      request,
      delivery_email: userEmail,
      enabled: true,
    }).select("id").single();
    if (error) throw error;
    const dispatched = await wakeWorker(data.id);
    setToast(dispatched ? "감시 요청을 등록하고 즉시 분석을 시작했습니다." : "감시 요청을 등록했습니다. 예약 작업에서 곧 분석합니다.");
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
      await wakeWorker(watch.id);
    }
    setWatches((current) => current.map((item) => item.id === watch.id ? { ...item, enabled } : item));
    setToast(enabled ? "감시를 다시 시작했습니다." : "감시를 일시정지했습니다.");
  }

  async function saveEdit(watch: WatchRequest, request: string, deliveryEmail: string) {
    if (!demoMode) {
      const { error } = await supabase
        .from("watch_requests")
        .update({ request, delivery_email: userEmail })
        .eq("id", watch.id);
      if (error) throw error;
      await wakeWorker(watch.id);
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

  async function markNotificationRead(notification: UserNotification) {
    if (notification.read_at || demoMode) return;
    const readAt = new Date().toISOString();
    const { error } = await supabase
      .from("user_notifications")
      .update({ read_at: readAt })
      .eq("id", notification.id);
    if (error) {
      setToast(error.message);
      return;
    }
    setNotifications((current) => current.map((item) => item.id === notification.id ? { ...item, read_at: readAt } : item));
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="header-inner">
          <Brand compact />
          <nav className="app-tabs" aria-label="주요 화면">
            <button className={view === "watches" ? "active" : ""} onClick={() => setView("watches")}>
              <ListChecks size={17} />감시
            </button>
            <button className={view === "notifications" ? "active" : ""} onClick={() => setView("notifications")}>
              <BellRing size={17} />알림
              {unreadCount > 0 && <span>{unreadCount}</span>}
            </button>
          </nav>
          <div className="account-area">
            <ServiceBadge health={health} />
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
              <p className="eyebrow">{view === "watches" ? "내 공지 감시" : "판정 및 발송 기록"}</p>
              <h1>{view === "watches" ? "놓치면 안 되는 조건" : "내 알림"}</h1>
            </div>
            <div className="metrics" aria-label="감시 상태 요약">
              {view === "watches" ? (
                <>
                  <Metric icon={<BellRing size={18} />} label="감시 중" value={activeCount} tone="green" />
                  <Metric icon={<Clock3 size={18} />} label="분석 중" value={waitingCount} tone="amber" />
                  <Metric icon={<Pause size={18} />} label="일시정지" value={pausedCount} tone="neutral" />
                </>
              ) : (
                <>
                  <Metric icon={<Inbox size={18} />} label="전체 알림" value={notifications.length} tone="green" />
                  <Metric icon={<BellRing size={18} />} label="읽지 않음" value={unreadCount} tone="amber" />
                  <Metric icon={<CircleAlert size={18} />} label="확인 필요" value={notifications.filter((item) => item.classification === "uncertain" || item.delivery_status === "needs_attention").length} tone="neutral" />
                </>
              )}
            </div>
          </div>
        </section>

        {view === "watches" ? (
          <>
            <section className="composer-band">
              <div className="content-width">
                <WatchComposer defaultEmail={userEmail} onCreate={createWatch} limitReached={activeCount >= 10} />
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
                      <WatchRow key={watch.id} watch={watch} onToggle={() => toggleWatch(watch)} onEdit={() => setEditing(watch)} />
                    ))}
                  </div>
                ) : (
                  <EmptyState icon={<Inbox size={22} />} text="등록된 감시가 없습니다" />
                )}
              </div>
            </section>
          </>
        ) : (
          <section className="notifications-band">
            <div className="content-width">
              <div className="list-toolbar">
                <strong>최근 100개</strong>
                <button className="icon-button" title="새로고침" onClick={loadNotifications}>
                  <RefreshCw size={18} />
                </button>
              </div>
              {notifications.length ? (
                <div className="notification-list">
                  {notifications.map((notification) => (
                    <NotificationRow key={notification.id} notification={notification} onRead={() => markNotificationRead(notification)} />
                  ))}
                </div>
              ) : (
                <EmptyState icon={<BellRing size={22} />} text="아직 도착한 알림이 없습니다" />
              )}
            </div>
          </section>
        )}
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

function ServiceBadge({ health }: { health: ServiceHealth | null }) {
  const status = health?.status ?? "degraded";
  const label = status === "healthy" ? "정상" : status === "unhealthy" ? "점검 필요" : "확인 중";
  return (
    <span className={`service-badge ${status}`} title={health?.summary ?? "서비스 상태를 아직 확인하지 않았습니다."}>
      {status === "unhealthy" ? <CircleAlert size={14} /> : <ShieldCheck size={14} />}{label}
    </span>
  );
}

function EmptyState({ icon, text }: { icon: React.ReactNode; text: string }) {
  return <div className="empty-state"><span className="icon-well">{icon}</span><strong>{text}</strong></div>;
}

function NotificationRow({ notification, onRead }: { notification: UserNotification; onRead: () => void }) {
  const facts = parseJson<AlertFact[]>(notification.facts_json, []);
  const evidence = parseJson<AlertEvidence[]>(notification.evidence_json, []);
  const uncertain = notification.classification === "uncertain";
  const delivery = deliveryMeta(notification.delivery_status);
  return (
    <article className={`notification-row ${notification.read_at ? "" : "unread"}`}>
      <div className={`status-rail ${uncertain ? "amber" : "green"}`} />
      <div className="notification-copy">
        <div className="watch-meta">
          <span className={`status-badge ${uncertain ? "amber" : "green"}`}>
            {uncertain ? <CircleAlert size={13} /> : <Check size={13} />}
            {uncertain ? "확인 필요" : "조건 충족"}
          </span>
          <span className={`delivery-status ${delivery.tone}`}>{delivery.label}</span>
          <span>{formatDate(notification.created_at)}</span>
        </div>
        <h2>{notification.title}</h2>
        <p className="notification-summary">{notification.summary}</p>
        {facts.length > 0 && (
          <ul className="fact-list">
            {facts.slice(0, 5).map((fact, index) => <li key={`${notification.id}-fact-${index}`}>{fact.text}</li>)}
          </ul>
        )}
        {evidence.length > 0 && (
          <div className="evidence-list" aria-label="관련 근거">
            {evidence.slice(0, 8).map((item) => (
              <span key={`${notification.id}-${item.id}`}>
                <FileText size={13} />{item.source_name}{item.page ? ` ${item.page}쪽` : item.row ? ` ${item.row}행` : ""}
              </span>
            ))}
          </div>
        )}
        {notification.last_error && <p className="row-error"><CircleAlert size={14} />발송 또는 판정 상태를 확인해 주세요.</p>}
      </div>
      <div className="notification-actions">
        {!notification.read_at && <button className="icon-button" title="읽음으로 표시" onClick={onRead}><Check size={17} /></button>}
        {notification.notice_url && (
          <a className="icon-button" title="공지 원문 열기" href={notification.notice_url} target="_blank" rel="noreferrer">
            <ExternalLink size={17} />
          </a>
        )}
      </div>
    </article>
  );
}

function WatchComposer({ defaultEmail, onCreate, limitReached }: {
  defaultEmail: string;
  onCreate: (request: string, email: string) => Promise<void>;
  limitReached: boolean;
}) {
  const [request, setRequest] = useState("");
  const email = defaultEmail;
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (request.trim().length < 5 || limitReached) return;
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
        disabled={limitReached}
        required
      />
      <div className="composer-footer">
        <label className="inline-email">
          <Mail size={16} />
          <span className="sr-only">알림 이메일</span>
          <input type="email" value={email} readOnly required />
        </label>
        <div className="composer-actions">
          <span className="char-count">{request.length}/1000</span>
          <button className="primary-button" disabled={submitting || request.trim().length < 5 || limitReached}>
            {submitting ? <LoaderCircle className="spin" size={18} /> : <Plus size={18} />}
            등록
          </button>
        </div>
      </div>
      {limitReached && <p className="limit-note"><CircleAlert size={14} />활성 감시는 계정당 최대 10개입니다. 기존 감시를 일시정지한 뒤 등록하세요.</p>}
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
  const email = watch.delivery_email;
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
          <input id="edit-email" type="email" value={email} readOnly required />
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

function parseJson<T>(value: string, fallback: T): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

function deliveryMeta(status: UserNotification["delivery_status"]) {
  const labels = {
    not_applicable: { label: "메일 미발송", tone: "neutral" },
    queued: { label: "발송 대기", tone: "blue" },
    retry: { label: "재시도 중", tone: "amber" },
    sent: { label: "메일 발송됨", tone: "green" },
    needs_attention: { label: "발송 확인 필요", tone: "red" },
  } as const;
  return labels[status];
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
