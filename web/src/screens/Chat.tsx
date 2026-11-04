import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import {
  sendChatMessage,
  uploadImage,
  type ChatDraftContext,
  type DraftItem,
  type DraftPreview,
} from '../api/finances';
import DraftItemView, { type DraftEditInputs } from '../components/DraftItemView';
import { FileSpreadsheetIcon, PlusIcon, ReceiptIcon } from '../components/Icons';
import { t, useLanguage } from '../i18n';
import { missingFieldLabel } from '../utils/financeFormatters';
import chatWelcome from '../assets/chat-welcome.png';
import chatDecoration from '../assets/chat-decoration.png';

export type ChatMode = 'qa' | 'transaction';
export interface ChatTurn {
  role: 'user' | 'assistant';
  text: string;
}

interface ChatProps {
  csrfToken: string;
  busy: boolean;
  text: string;
  draft: DraftPreview | null;
  pendingDraft: DraftPreview | null;
  onResume: () => void;
  onNewDraft: () => void;
  onDraftItemChange: (item: DraftItem) => void;
  turns: ChatTurn[];
  onTurnsChange: (turns: ChatTurn[]) => void;
  dateInputs: Record<string, string>;
  periodInputs: Record<string, string>;
  textInputs: Record<string, DraftEditInputs>;
  onTextChange: (value: string) => void;
  onPreview: (draft: DraftPreview) => void;
  onBusyChange: (value: boolean) => void;
  onError: (message: string) => void;
  onDateChange: (id: string, value: string) => void;
  onPeriodChange: (id: string, value: string) => void;
  onDescriptionChange: (id: string, value: string) => void;
  onAmountChange: (id: string, value: string) => void;
  onCurrencyChange: (id: string, value: string) => void;
  onKindChange: (id: string, value: DraftItem['kind']) => void;
  onCategoryChange: (id: string, value: string) => void;
  onLocalTimeChange: (id: string, value: string) => void;
  onSaveFacts: (item: DraftItem) => void;
  onConfirm: (item: DraftItem) => void;
}

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

export default function Chat(props: ChatProps) {
  const { language } = useLanguage();
  const [mode, setMode] = useState<ChatMode>('qa');
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [duplicateDetected, setDuplicateDetected] = useState(false);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [messageIdempotencyKey, setMessageIdempotencyKey] = useState(() => crypto.randomUUID());
  const [selectedItem, setSelectedItem] = useState('');
  const [followups, setFollowups] = useState<ChatTurn[]>([]);
  const activeMode = props.draft ? 'transaction' : mode;
  const fileInput = useRef<HTMLInputElement>(null);
  const messageInput = useRef<HTMLTextAreaElement>(null);
  const incomplete = props.draft?.items.filter((item) => item.status === 'incomplete') ?? [];
  const target = incomplete.find((item) => item.id === selectedItem) ?? incomplete[0];
  const onlyMissingClockTime =
    target?.missingFields.length === 1 &&
    ['local_time', 'localTime'].includes(target.missingFields[0] ?? '');
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  function chooseImage(nextFile: File | null) {
    if (!nextFile) return;
    if (!IMAGE_TYPES.has(nextFile.type) || nextFile.size === 0 || nextFile.size > MAX_IMAGE_BYTES) {
      props.onError('Only JPEG, PNG or WebP images up to 10 MiB are supported.');
      return;
    }
    props.onError('');
    setMode('transaction');
    setFile(nextFile);
    setPreviewUrl(URL.createObjectURL(nextFile));
    setDuplicateDetected(false);
    setAllowDuplicate(false);
    setIdempotencyKey(crypto.randomUUID());
  }

  function pasteImage(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    const image = [...event.clipboardData.items]
      .find((item) => item.type.startsWith('image/'))
      ?.getAsFile();
    if (!image) return;
    event.preventDefault();
    const extension =
      image.type === 'image/jpeg' ? 'jpg' : image.type === 'image/webp' ? 'webp' : 'png';
    chooseImage(new File([image], `anh-dan.${extension}`, { type: image.type }));
  }

  async function submitMessage(draftContext?: ChatDraftContext, messageOverride?: string) {
    const message = (messageOverride ?? props.text).trim();
    if (!message) return;
    props.onBusyChange(true);
    props.onError('');
    try {
      const result = await sendChatMessage(
        message,
        language,
        props.csrfToken,
        props.turns,
        messageIdempotencyKey,
        draftContext,
      );
      if (result.intent === 'transaction') {
        setMode('transaction');
        props.onPreview(result.draft);
      } else if (result.intent === 'clarification') {
        props.onDraftItemChange(result.item);
        const reply = result.item.missingFields.length
          ? 'Updated. Please add the remaining details below.'
          : 'All required details are present. Review and confirm when ready.';
        setFollowups((current) => [
          ...current,
          { role: 'user', text: message },
          { role: 'assistant', text: reply },
        ]);
      } else {
        setMode('qa');
        const turns = [
          { role: 'user' as const, text: message },
          { role: 'assistant' as const, text: result.answer },
        ];
        if (props.draft) setFollowups((current) => [...current, ...turns]);
        else props.onTurnsChange([...props.turns, ...turns]);
      }
      props.onTextChange('');
      setMessageIdempotencyKey(crypto.randomUUID());
    } catch (reason) {
      props.onError(reason instanceof Error ? reason.message : 'Chat is unavailable.');
    } finally {
      props.onBusyChange(false);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (props.busy) return;
    if (props.draft && file) {
      props.onError('Choose “New transaction” to start another draft.');
      return;
    }
    if (!file) {
      const context =
        target && props.draft
          ? {
              draftId: props.draft.id,
              itemId: target.id,
              expectedVersion: target.version,
            }
          : undefined;
      return submitMessage(context);
    }
    if (props.text.trim() || (duplicateDetected && !allowDuplicate)) return;
    props.onBusyChange(true);
    props.onError('');
    try {
      const draft = await uploadImage(file, props.csrfToken, allowDuplicate, idempotencyKey);
      props.onPreview(draft);
      setMode('transaction');
      setFile(null);
      setPreviewUrl(null);
      setDuplicateDetected(false);
      setAllowDuplicate(false);
      setIdempotencyKey(crypto.randomUUID());
    } catch (reason) {
      if (reason instanceof ApiError && reason.statusCode === 409 && !allowDuplicate) {
        setDuplicateDetected(true);
        props.onError('Image already imported. Choose duplicate import only if intended.');
      } else {
        props.onError(reason instanceof Error ? reason.message : 'Could not read receipt image.');
      }
    } finally {
      props.onBusyChange(false);
    }
  }

  return (
    <div className="chat-view">
      <div className="chat-thread">
        {activeMode === 'qa' && props.turns.length === 0 && (
          <div className="chat-welcome">
            <img className="chat-welcome-image" src={chatWelcome} alt="" />
            <h1>Ready when you are.</h1>
            <p>Ask about your spending, saving habits, or financial insights.</p>
            {!props.text.trim() && (
              <div className="chat-suggestions" aria-label="Try asking">
                {[
                  'Summarize my spending this month',
                  'Where am I spending the most?',
                  'Help me plan a monthly budget',
                  'How can I improve my saving habits?',
                ].map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    disabled={props.busy}
                    onClick={() => void submitMessage(undefined, prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
            <img className="chat-decoration" src={chatDecoration} alt="" />
          </div>
        )}
        {activeMode === 'qa' && props.turns.length > 0 && (
          <div className="chat-turn-list" role="log" aria-label="Q&A conversation">
            {props.turns.map((turn, index) => (
              <div
                key={index}
                className={`chat-bubble ${turn.role === 'user' ? 'chat-user-bubble' : 'chat-assistant-bubble'}`}
              >
                <span className="chat-turn-speaker">
                  {turn.role === 'user' ? 'You' : 'Where my bucks go?'}
                </span>
                <p>{turn.text}</p>
              </div>
            ))}
          </div>
        )}
        {activeMode === 'transaction' && !props.draft && (
          <div className="chat-welcome">
            <h1>What would you like to record?</h1>
            {props.pendingDraft && (
              <button type="button" className="btn-secondary chat-resume" onClick={props.onResume}>
                Resume pending draft
              </button>
            )}
          </div>
        )}
        {activeMode === 'transaction' && props.draft && (
          <div className="chat-conversation">
            <div className="chat-bubble chat-user-bubble">
              {props.draft.evidence.mediaType?.startsWith('image/') &&
                props.draft.evidence.contentUrl && (
                  <img src={props.draft.evidence.contentUrl} alt="Submitted receipt" />
                )}
              <p>
                {props.draft.evidence.sourceText ??
                  props.draft.evidence.originalFilename ??
                  'Receipt image'}
              </p>
            </div>
            {incomplete.length > 0 && (
              <div className="chat-bubble chat-assistant-bubble" role="status">
                <p>
                  {onlyMissingClockTime
                    ? 'What time did this happen?'
                    : `I still need: ${target?.missingFields
                        .map((field) => t(language, missingFieldLabel(field)))
                        .join(', ')}.`}
                </p>
                <span className="chat-turn-speaker">Where my bucks go?</span>
                {incomplete.length > 1 && (
                  <label className="chat-item-picker">
                    Reply about item
                    <select
                      value={target?.id ?? ''}
                      onChange={(event) => setSelectedItem(event.currentTarget.value)}
                    >
                      {incomplete.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.index + 1}. {item.description ?? 'No description'}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            )}
            <section className="bento-card chat-draft" aria-labelledby="chat-draft-title">
              <h2 id="chat-draft-title" className="card-title">
                Review transaction
              </h2>
              <details className="source-evidence-accordion">
                <summary>
                  <ReceiptIcon size={16} /> View source
                </summary>
                <div className="evidence-accordion-content">
                  {props.draft.evidence.sourceText && (
                    <blockquote className="evidence-quote">
                      {props.draft.evidence.sourceText}
                    </blockquote>
                  )}
                  {props.draft.evidence.mediaType?.startsWith('image/') &&
                    props.draft.evidence.contentUrl && (
                      <img
                        className="evidence-img"
                        src={props.draft.evidence.contentUrl}
                        alt="Original receipt image"
                      />
                    )}
                  {props.draft.evidence.sourceType === 'csv' && props.draft.evidence.contentUrl && (
                    <a className="evidence-link" href={props.draft.evidence.contentUrl} download>
                      <FileSpreadsheetIcon size={16} /> Download CSV source
                    </a>
                  )}
                </div>
              </details>
              <div className="draft-items-list">
                {props.draft.items.map((item) => (
                  <DraftItemView
                    key={item.id}
                    item={item}
                    busy={props.busy}
                    dateValue={props.dateInputs[item.id] ?? item.transactionDate ?? ''}
                    periodValue={props.periodInputs[item.id] ?? item.dayPeriod ?? ''}
                    descriptionValue={
                      props.textInputs[item.id]?.description ?? item.description ?? ''
                    }
                    amountValue={props.textInputs[item.id]?.amount ?? item.amount?.amount ?? ''}
                    currencyValue={props.textInputs[item.id]?.currency ?? item.currency ?? ''}
                    kindValue={props.textInputs[item.id]?.kind ?? item.kind}
                    categoryValue={props.textInputs[item.id]?.category ?? item.category}
                    localTimeValue={props.textInputs[item.id]?.localTime ?? item.localTime ?? ''}
                    onDateChange={(value) => props.onDateChange(item.id, value)}
                    onPeriodChange={(value) => props.onPeriodChange(item.id, value)}
                    onDescriptionChange={(value) => props.onDescriptionChange(item.id, value)}
                    onAmountChange={(value) => props.onAmountChange(item.id, value)}
                    onCurrencyChange={(value) => props.onCurrencyChange(item.id, value)}
                    onKindChange={(value) => props.onKindChange(item.id, value)}
                    onCategoryChange={(value) => props.onCategoryChange(item.id, value)}
                    onLocalTimeChange={(value) => props.onLocalTimeChange(item.id, value)}
                    onSave={() => props.onSaveFacts(item)}
                    onConfirm={() => props.onConfirm(item)}
                  />
                ))}
              </div>
            </section>
            {followups.map((turn, index) => (
              <div
                key={index}
                className={`chat-bubble ${turn.role === 'user' ? 'chat-user-bubble' : 'chat-assistant-bubble'}`}
              >
                <span className="chat-turn-speaker">
                  {turn.role === 'user' ? 'You' : 'Where my bucks go?'}
                </span>
                <p>{turn.text}</p>
              </div>
            ))}
            <button
              type="button"
              className="btn-secondary chat-new-button"
              onClick={() => {
                setFollowups([]);
                props.onNewDraft();
              }}
            >
              New transaction
            </button>
          </div>
        )}
      </div>
      <form
        className={`chat-composer ${activeMode === 'transaction' && props.draft ? 'has-draft' : ''}`}
        onSubmit={(event) => void submit(event)}
      >
        {previewUrl && file && (
          <div className="chat-attachment">
            <img src={previewUrl} alt="Image to upload" />
            <span>{file.name}</span>
            <button
              type="button"
              aria-label="Remove attached image"
              onClick={() => {
                setFile(null);
                setPreviewUrl(null);
              }}
            >
              ×
            </button>
          </div>
        )}
        <label className="visually-hidden" htmlFor="chat-message">
          Message Where my bucks go?
        </label>
        <textarea
          ref={messageInput}
          id="chat-message"
          value={props.text}
          onChange={(event) => {
            props.onTextChange(event.currentTarget.value);
            setMessageIdempotencyKey(crypto.randomUUID());
          }}
          onPaste={pasteImage}
          rows={1}
          maxLength={8_000}
          placeholder="Message"
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        {activeMode === 'transaction' && file && props.text.trim() && (
          <p className="chat-hint" role="status">
            Images and text are processed separately. Send one at a time.
          </p>
        )}
        {activeMode === 'transaction' && duplicateDetected && (
          <label className="chat-duplicate">
            <input
              type="checkbox"
              checked={allowDuplicate}
              onChange={(event) => {
                setAllowDuplicate(event.currentTarget.checked);
                setIdempotencyKey(crypto.randomUUID());
              }}
            />
            I want to create another draft from this image.
          </label>
        )}
        <div className="chat-composer-actions">
          <input
            ref={fileInput}
            className="visually-hidden"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            aria-label="Choose receipt image"
            onChange={(event) => {
              chooseImage(event.currentTarget.files?.[0] ?? null);
              event.currentTarget.value = '';
            }}
          />
          <button
            type="button"
            className="chat-attach-button"
            aria-label="Attach image"
            title="Attach image"
            onClick={() => fileInput.current?.click()}
            disabled={props.busy}
          >
            <PlusIcon size={20} />
          </button>
          <span className="chat-composer-caption" aria-hidden="true" />
          <button
            type="submit"
            className="chat-send-button"
            aria-label="Send message"
            disabled={
              props.busy ||
              !(file || props.text.trim()) ||
              Boolean(file && props.text.trim()) ||
              (Boolean(file) && duplicateDetected && !allowDuplicate)
            }
          >
            {props.busy ? 'Working…' : 'Send'}
          </button>
        </div>
      </form>
    </div>
  );
}
