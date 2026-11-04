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
  itemEdits: Record<string, DraftEditInputs>;
  onTextChange: (value: string) => void;
  onItemEditChange: (id: string, field: keyof DraftEditInputs, value: string) => void;
  onPreview: (draft: DraftPreview) => void;
  onBusyChange: (value: boolean) => void;
  onError: (message: string) => void;
  onSaveFacts: (item: DraftItem) => void;
  onConfirm: (item: DraftItem) => Promise<void>;
}

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

export default function Chat(props: ChatProps) {
  const [mode, setMode] = useState<ChatMode>('qa');
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [duplicateDetected, setDuplicateDetected] = useState(false);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [messageIdempotencyKey, setMessageIdempotencyKey] = useState(() => crypto.randomUUID());
  const [selectedItem, setSelectedItem] = useState('');
  const [followups, setFollowups] = useState<ChatTurn[]>([]);
  const [confirmingAll, setConfirmingAll] = useState(false);
  const activeMode = props.draft ? 'transaction' : mode;
  const readyItems = props.draft?.items.filter((item) => item.status === 'ready') ?? [];
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
      props.onError('Chỉ hỗ trợ ảnh JPEG, PNG hoặc WebP tối đa 10 MiB.');
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
        'vi',
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
          ? 'Đã cập nhật. Vui lòng bổ sung thông tin còn thiếu bên dưới.'
          : 'Đã đủ thông tin. Hãy xem lại và xác nhận khi sẵn sàng.';
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
      props.onError(reason instanceof Error ? reason.message : 'Trò chuyện hiện không khả dụng.');
    } finally {
      props.onBusyChange(false);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (props.busy) return;
    if (props.draft && file) {
      props.onError('Chọn "Giao dịch mới" để tạo bản nháp khác.');
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
        props.onError('Ảnh này đã được nhập trước đó. Chỉ chọn nhập lại nếu bạn chắc chắn.');
      } else {
        props.onError(reason instanceof Error ? reason.message : 'Không đọc được ảnh hoá đơn.');
      }
    } finally {
      props.onBusyChange(false);
    }
  }

  async function confirmAllReady() {
    setConfirmingAll(true);
    try {
      for (const item of readyItems) {
        await props.onConfirm(item);
      }
    } finally {
      setConfirmingAll(false);
    }
  }

  return (
    <div className="chat-view">
      <div className="chat-thread">
        {activeMode === 'qa' && props.turns.length === 0 && (
          <div className="chat-welcome">
            <img className="chat-welcome-image" src={chatWelcome} alt="" />
            <h1>Sẵn sàng khi bạn cần.</h1>
            <p>Hỏi về chi tiêu, thói quen tiết kiệm hoặc phân tích tài chính của bạn.</p>
            <img className="chat-decoration" src={chatDecoration} alt="" />
          </div>
        )}
        {activeMode === 'qa' && props.turns.length > 0 && (
          <div className="chat-turn-list" role="log" aria-label="Hội thoại hỏi đáp">
            {props.turns.map((turn, index) => (
              <div
                key={index}
                className={`chat-bubble ${turn.role === 'user' ? 'chat-user-bubble' : 'chat-assistant-bubble'}`}
              >
                <span className="chat-turn-speaker">
                  {turn.role === 'user' ? 'Bạn' : 'Where my bucks go?'}
                </span>
                <p>{turn.text}</p>
              </div>
            ))}
          </div>
        )}
        {activeMode === 'transaction' && !props.draft && (
          <div className="chat-welcome">
            <h1>Bạn muốn ghi lại khoản gì?</h1>
            {props.pendingDraft && (
              <button type="button" className="btn-secondary chat-resume" onClick={props.onResume}>
                Tiếp tục bản nháp đang chờ
              </button>
            )}
          </div>
        )}
        {activeMode === 'transaction' && props.draft && (
          <div className="chat-conversation">
            <div className="chat-bubble chat-user-bubble">
              {props.draft.evidence.mediaType?.startsWith('image/') &&
                props.draft.evidence.contentUrl && (
                  <img src={props.draft.evidence.contentUrl} alt="Ảnh hoá đơn đã gửi" />
                )}
              <p>
                {props.draft.evidence.sourceText ??
                  props.draft.evidence.originalFilename ??
                  'Ảnh hoá đơn'}
              </p>
            </div>
            {incomplete.length > 0 && (
              <div className="chat-bubble chat-assistant-bubble" role="status">
                <p>
                  {onlyMissingClockTime
                    ? 'Việc này xảy ra vào lúc mấy giờ?'
                    : `Còn thiếu: ${target?.missingFields
                        .map((field) => missingFieldLabel(field))
                        .join(', ')}.`}
                </p>
                <span className="chat-turn-speaker">Where my bucks go?</span>
                {incomplete.length > 1 && (
                  <label className="chat-item-picker">
                    Trả lời về khoản
                    <select
                      value={target?.id ?? ''}
                      onChange={(event) => setSelectedItem(event.currentTarget.value)}
                    >
                      {incomplete.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.index + 1}. {item.description ?? 'Chưa có nội dung'}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            )}
            <section className="bento-card chat-draft" aria-labelledby="chat-draft-title">
              <div className="chat-draft-heading">
                <h2 id="chat-draft-title" className="card-title">
                  Xem lại giao dịch
                </h2>
                {readyItems.length > 1 && (
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => void confirmAllReady()}
                    disabled={props.busy || confirmingAll}
                  >
                    {confirmingAll ? 'Đang xác nhận…' : `Xác nhận tất cả (${readyItems.length})`}
                  </button>
                )}
              </div>
              <details className="source-evidence-accordion">
                <summary>
                  <ReceiptIcon size={16} /> Xem chứng từ gốc
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
                        alt="Ảnh hoá đơn gốc"
                      />
                    )}
                  {props.draft.evidence.sourceType === 'csv' && props.draft.evidence.contentUrl && (
                    <a className="evidence-link" href={props.draft.evidence.contentUrl} download>
                      <FileSpreadsheetIcon size={16} /> Tải tệp CSV gốc
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
                    dateValue={
                      props.itemEdits[item.id]?.transactionDate ?? item.transactionDate ?? ''
                    }
                    periodValue={props.itemEdits[item.id]?.dayPeriod ?? item.dayPeriod ?? ''}
                    descriptionValue={
                      props.itemEdits[item.id]?.description ?? item.description ?? ''
                    }
                    amountValue={props.itemEdits[item.id]?.amount ?? item.amount?.amount ?? ''}
                    currencyValue={props.itemEdits[item.id]?.currency ?? item.currency ?? ''}
                    kindValue={props.itemEdits[item.id]?.kind ?? item.kind}
                    categoryValue={props.itemEdits[item.id]?.category ?? item.category}
                    localTimeValue={props.itemEdits[item.id]?.localTime ?? item.localTime ?? ''}
                    onDateChange={(value) =>
                      props.onItemEditChange(item.id, 'transactionDate', value)
                    }
                    onPeriodChange={(value) => props.onItemEditChange(item.id, 'dayPeriod', value)}
                    onDescriptionChange={(value) =>
                      props.onItemEditChange(item.id, 'description', value)
                    }
                    onAmountChange={(value) => props.onItemEditChange(item.id, 'amount', value)}
                    onCurrencyChange={(value) => props.onItemEditChange(item.id, 'currency', value)}
                    onKindChange={(value) => props.onItemEditChange(item.id, 'kind', value)}
                    onCategoryChange={(value) => props.onItemEditChange(item.id, 'category', value)}
                    onLocalTimeChange={(value) =>
                      props.onItemEditChange(item.id, 'localTime', value)
                    }
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
                  {turn.role === 'user' ? 'Bạn' : 'Where my bucks go?'}
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
              Giao dịch mới
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
            <img src={previewUrl} alt="Ảnh sẽ được gửi" />
            <span>{file.name}</span>
            <button
              type="button"
              aria-label="Xoá ảnh đã đính kèm"
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
          Nhắn cho Where my bucks go?
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
          placeholder="Nhắn tin…"
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        {activeMode === 'transaction' && file && props.text.trim() && (
          <p className="chat-hint" role="status">
            Ảnh và văn bản được xử lý riêng. Gửi từng loại một lần.
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
            Tôi muốn tạo bản nháp khác từ ảnh này.
          </label>
        )}
        <div className="chat-composer-actions">
          <input
            ref={fileInput}
            className="visually-hidden"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            aria-label="Chọn ảnh hoá đơn"
            onChange={(event) => {
              chooseImage(event.currentTarget.files?.[0] ?? null);
              event.currentTarget.value = '';
            }}
          />
          <button
            type="button"
            className="chat-attach-button"
            aria-label="Đính kèm ảnh"
            title="Đính kèm ảnh"
            onClick={() => fileInput.current?.click()}
            disabled={props.busy}
          >
            <PlusIcon size={20} />
          </button>
          <span className="chat-composer-caption" aria-hidden="true" />
          <button
            type="submit"
            className="chat-send-button"
            aria-label="Gửi tin nhắn"
            disabled={
              props.busy ||
              !(file || props.text.trim()) ||
              Boolean(file && props.text.trim()) ||
              (Boolean(file) && duplicateDetected && !allowDuplicate)
            }
          >
            {props.busy ? 'Đang xử lý…' : 'Gửi'}
          </button>
        </div>
      </form>
    </div>
  );
}
