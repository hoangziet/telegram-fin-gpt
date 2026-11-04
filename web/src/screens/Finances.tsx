import { useEffect, useMemo, useRef, useState } from 'react';
import {
  confirmDraftItem,
  getEvidence,
  getTotals,
  listOpenDrafts,
  listTransactions,
  updateDraftItem,
  type DraftItem,
  type DraftPreview,
  type EvidenceRecord,
  type TotalsReport,
  type TransactionRecord,
} from '../api/finances';
import type { DraftEditInputs } from '../components/DraftItemView';
import Dashboard from './Dashboard';
import Chat from './Chat';
import type { ChatTurn } from './Chat';
import History from './History';
import {
  currentMonthRange,
  readViewFromUrl,
  messageFor,
  type View,
} from '../utils/financeFormatters';
import {
  DashboardIcon,
  HistoryIcon,
  SignOutIcon,
  AlertCircleIcon,
  ChatIcon,
} from '../components/Icons';
import '../product.css';
import './transaction-history.css';
import '../reference-theme.css';
import './chat.css';
import '../responsive-theme.css';
import { t, useLanguage } from '../i18n';
import appIcon from '../assets/icon-512.png';

interface FinancesProps {
  csrfToken: string;
  username: string;
  isSubmitting: boolean;
  errorMessage: string;
  onLogout: () => Promise<void>;
}

export default function Finances({
  csrfToken,
  username,
  isSubmitting,
  errorMessage,
  onLogout,
}: FinancesProps) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const [view, setView] = useState<View>(() => readViewFromUrl());
  const [transactions, setTransactions] = useState<TransactionRecord[]>([]);
  const [report, setReport] = useState<TotalsReport | null>(null);
  const [draft, setDraft] = useState<DraftPreview | null>(null);
  const [pendingDraft, setPendingDraft] = useState<DraftPreview | null>(null);
  const [text, setText] = useState('');
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [query, setQuery] = useState(
    () => new URLSearchParams(window.location.search).get('q') ?? '',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [evidence, setEvidence] = useState<EvidenceRecord | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [dateInputs, setDateInputs] = useState<Record<string, string>>({});
  const [periodInputs, setPeriodInputs] = useState<Record<string, string>>({});
  const [textInputs, setTextInputs] = useState<Record<string, DraftEditInputs>>({});
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const accountTriggerRef = useRef<HTMLButtonElement>(null);
  const signOutRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;

    function closeOnOutsidePointer(event: PointerEvent) {
      if (event.target instanceof Node && !accountMenuRef.current?.contains(event.target)) {
        setAccountMenuOpen(false);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      setAccountMenuOpen(false);
      accountTriggerRef.current?.focus();
    }

    document.addEventListener('pointerdown', closeOnOutsidePointer);
    document.addEventListener('keydown', closeOnEscape);
    signOutRef.current?.focus();
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [accountMenuOpen]);

  const monthRange = useMemo(() => currentMonthRange(), []);
  useEffect(() => {
    const syncFromUrl = () => {
      setView(readViewFromUrl());
      setQuery(new URLSearchParams(window.location.search).get('q') ?? '');
    };
    window.addEventListener('popstate', syncFromUrl);
    return () => window.removeEventListener('popstate', syncFromUrl);
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set('view', view);
    if (query) url.searchParams.set('q', query);
    else url.searchParams.delete('q');
    window.history.replaceState({}, '', url);
  }, [view, query]);

  useEffect(() => {
    let current = true;
    Promise.all([listTransactions(), getTotals(monthRange.from, monthRange.to)])
      .then(([page, totals]) => {
        if (!current) return;
        setTransactions(page.items);
        setReport(totals);
      })
      .catch((reason: unknown) => {
        if (current) {
          setError(messageFor(reason));
        }
      });
    return () => {
      current = false;
    };
  }, [monthRange]);

  useEffect(() => {
    let current = true;
    listOpenDrafts()
      .then((drafts) => {
        if (current) setPendingDraft(drafts[0] ?? null);
      })
      .catch((reason: unknown) => {
        if (current) setError(messageFor(reason));
      });
    return () => {
      current = false;
    };
  }, []);

  async function saveMissingFacts(item: DraftItem) {
    if (!draft) return;
    setBusy(true);
    setError('');
    try {
      const updated = await updateDraftItem(
        draft.id,
        item,
        {
          transactionDate: dateInputs[item.id] ?? item.transactionDate ?? '',
          dayPeriod: periodInputs[item.id] ?? item.dayPeriod ?? '',
          description: textInputs[item.id]?.description ?? item.description ?? '',
          amount: textInputs[item.id]?.amount ?? item.amount?.amount ?? '',
          currency: textInputs[item.id]?.currency ?? item.currency ?? '',
          localTime: textInputs[item.id]?.localTime ?? item.localTime ?? '',
          kind: textInputs[item.id]?.kind ?? item.kind,
          category: textInputs[item.id]?.category ?? item.category,
        },
        csrfToken,
      );
      setDraft({
        ...draft,
        items: draft.items.map((candidate) => (candidate.id === item.id ? updated : candidate)),
      });
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setBusy(false);
    }
  }

  function acceptClarifiedItem(updated: DraftItem) {
    setDraft((current) =>
      current
        ? {
            ...current,
            items: current.items.map((candidate) =>
              candidate.id === updated.id ? updated : candidate,
            ),
          }
        : current,
    );
    setTextInputs((current) => {
      const next = { ...current };
      delete next[updated.id];
      return next;
    });
    setDateInputs((current) => {
      const next = { ...current };
      delete next[updated.id];
      return next;
    });
    setPeriodInputs((current) => {
      const next = { ...current };
      delete next[updated.id];
      return next;
    });
  }

  async function confirmItem(item: DraftItem) {
    if (!draft) return;
    setBusy(true);
    setError('');
    try {
      await confirmDraftItem(draft.id, item, csrfToken);
      setDraft({
        ...draft,
        items: draft.items.map((candidate) =>
          candidate.id === item.id ? { ...candidate, status: 'confirmed' } : candidate,
        ),
      });
      await refreshTransactions();
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setBusy(false);
    }
  }

  async function openEvidence(transactionId: string) {
    setEvidenceLoading(true);
    setError('');
    try {
      setEvidence(await getEvidence(transactionId));
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setEvidenceLoading(false);
    }
  }

  async function refreshTransactions() {
    try {
      const [page, totals] = await Promise.all([
        listTransactions(),
        getTotals(monthRange.from, monthRange.to),
      ]);
      setTransactions(page.items);
      setReport(totals);
    } catch (reason) {
      setError(messageFor(reason));
    }
  }

  return (
    <div className="finance-shell" data-theme="finance" lang={language}>
      {/* Modern Sidebar */}
      <aside className="product-sidebar">
        <div className="brand-header">
          <a className="wordmark" href="/?view=dashboard" aria-label="Where my bucks go? home">
            <img className="logo-badge" src={appIcon} alt="" />
            <span className="brand-name">Where my bucks go?</span>
            <span className="ai-chip">AI v2</span>
          </a>
        </div>

        <nav className="main-nav" aria-label="Main navigation">
          <button
            type="button"
            className={`nav-button ${view === 'dashboard' ? 'is-active' : ''}`}
            aria-current={view === 'dashboard' ? 'page' : undefined}
            onClick={() => setView('dashboard')}
          >
            <DashboardIcon size={20} />
            <span>{label('Tổng quan')}</span>
          </button>
          <button
            type="button"
            className={`nav-button ${view === 'chat' ? 'is-active' : ''}`}
            aria-current={view === 'chat' ? 'page' : undefined}
            onClick={() => setView('chat')}
          >
            <ChatIcon size={20} />
            <span>{label('Trò chuyện')}</span>
          </button>
          <button
            type="button"
            className={`nav-button ${view === 'history' ? 'is-active' : ''}`}
            aria-current={view === 'history' ? 'page' : undefined}
            onClick={() => setView('history')}
          >
            <HistoryIcon size={20} />
            <span>{label('Giao dịch')}</span>
          </button>
        </nav>

        {/* User Card */}
        <div className="account-card" ref={accountMenuRef}>
          <button
            ref={accountTriggerRef}
            className="account-trigger"
            type="button"
            aria-label={`Account menu for ${username}`}
            aria-haspopup="menu"
            aria-expanded={accountMenuOpen}
            aria-controls="account-menu"
            onClick={() => setAccountMenuOpen((open) => !open)}
          >
            <span className="user-profile">
              <span className="user-avatar" aria-hidden="true">
                {username.slice(0, 1).toUpperCase()}
              </span>
              <span className="user-meta">
                <span className="user-name" title={username}>
                  {username}
                </span>
                <span className="user-role">Account owner</span>
              </span>
            </span>
            <svg className="account-chevron" viewBox="0 0 16 16" aria-hidden="true">
              <path d="m4 6 4 4 4-4" />
            </svg>
          </button>
          {accountMenuOpen && (
            <div className="account-menu" id="account-menu" role="menu" aria-label="Account">
              <button
                ref={signOutRef}
                className="account-menu-item"
                role="menuitem"
                type="button"
                onClick={() => {
                  setAccountMenuOpen(false);
                  void onLogout();
                }}
                disabled={isSubmitting}
              >
                <SignOutIcon size={17} />
                <span>{isSubmitting ? 'Signing out…' : 'Sign out'}</span>
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="product-main">
        {errorMessage && (
          <div className="system-alert alert-error" role="alert">
            <AlertCircleIcon size={18} />
            <span>{label(errorMessage)}</span>
          </div>
        )}
        {error && (
          <div className="system-alert alert-error" role="alert">
            <AlertCircleIcon size={18} />
            <span>{label(error)}</span>
          </div>
        )}

        {view === 'dashboard' && (
          <Dashboard
            transactions={transactions.filter((item) => !item.isDeleted).slice(0, 6)}
            report={report}
          />
        )}

        {view === 'chat' && (
          <Chat
            csrfToken={csrfToken}
            busy={busy}
            draft={draft}
            pendingDraft={pendingDraft}
            onResume={() => {
              if (pendingDraft) {
                setDraft(pendingDraft);
                setPendingDraft(null);
                setView('chat');
              }
            }}
            onNewDraft={() => {
              if (
                draft?.items.some((item) => item.status === 'incomplete' || item.status === 'ready')
              ) {
                setPendingDraft((current) => current ?? draft);
              }
              setDraft(null);
            }}
            onDraftItemChange={acceptClarifiedItem}
            turns={chatTurns}
            onTurnsChange={setChatTurns}
            text={text}
            dateInputs={dateInputs}
            periodInputs={periodInputs}
            textInputs={textInputs}
            onTextChange={setText}
            onDateChange={(id, value) => setDateInputs({ ...dateInputs, [id]: value })}
            onPeriodChange={(id, value) => setPeriodInputs({ ...periodInputs, [id]: value })}
            onDescriptionChange={(id, value) =>
              setTextInputs({
                ...textInputs,
                [id]: { ...textInputs[id], description: value },
              })
            }
            onAmountChange={(id, value) =>
              setTextInputs({
                ...textInputs,
                [id]: { ...textInputs[id], amount: value },
              })
            }
            onCurrencyChange={(id, value) =>
              setTextInputs({
                ...textInputs,
                [id]: { ...textInputs[id], currency: value },
              })
            }
            onKindChange={(id, value) =>
              setTextInputs({ ...textInputs, [id]: { ...textInputs[id], kind: value } })
            }
            onCategoryChange={(id, value) =>
              setTextInputs({ ...textInputs, [id]: { ...textInputs[id], category: value } })
            }
            onLocalTimeChange={(id, value) =>
              setTextInputs({ ...textInputs, [id]: { ...textInputs[id], localTime: value } })
            }
            onPreview={(nextDraft) => {
              if (
                draft &&
                draft.items.some((item) => item.status === 'incomplete' || item.status === 'ready')
              ) {
                setPendingDraft((current) => current ?? draft);
              }
              setDraft(nextDraft);
              setEvidence(null);
            }}
            onBusyChange={setBusy}
            onSaveFacts={saveMissingFacts}
            onConfirm={confirmItem}
            onError={setError}
          />
        )}

        {view === 'history' && (
          <History
            csrfToken={csrfToken}
            query={query}
            onQueryChange={(value) => {
              setError('');
              setQuery(value);
            }}
            onEvidence={openEvidence}
            evidence={evidence}
            evidenceLoading={evidenceLoading}
            onCloseEvidence={() => setEvidence(null)}
            onChanged={refreshTransactions}
            onError={setError}
          />
        )}
      </main>

      {/* Mobile Bottom Navigation Bar */}
      <nav className="mobile-bottom-nav" aria-label="Mobile navigation">
        <button
          type="button"
          className={`mobile-nav-item ${view === 'dashboard' ? 'is-active' : ''}`}
          aria-current={view === 'dashboard' ? 'page' : undefined}
          onClick={() => setView('dashboard')}
        >
          <DashboardIcon size={20} />
          <span>{label('Tổng quan')}</span>
        </button>
        <button
          type="button"
          className={`mobile-nav-item ${view === 'chat' ? 'is-active' : ''}`}
          aria-current={view === 'chat' ? 'page' : undefined}
          onClick={() => setView('chat')}
        >
          <ChatIcon size={20} />
          <span>{label('Trò chuyện')}</span>
        </button>
        <button
          type="button"
          className={`mobile-nav-item ${view === 'history' ? 'is-active' : ''}`}
          aria-current={view === 'history' ? 'page' : undefined}
          onClick={() => setView('history')}
        >
          <HistoryIcon size={20} />
          <span>{label('Giao dịch')}</span>
        </button>
      </nav>
    </div>
  );
}
