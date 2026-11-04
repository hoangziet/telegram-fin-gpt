import { useState } from 'react';
import { ApiError } from '../api/client';
import { uploadCsv, type DraftPreview } from '../api/finances';
import { t, useLanguage } from '../i18n';

const FIELD_OPTIONS = [
  {
    key: 'amount',
    label: 'Số tiền',
    required: true,
    aliases: ['amount', 'số tiền', 'so_tien', 'total'],
  },
  {
    key: 'description',
    label: 'Nội dung',
    required: true,
    aliases: ['description', 'nội dung', 'ghi chú', 'memo', 'note'],
  },
  { key: 'transactionDate', label: 'Ngày', aliases: ['date', 'ngày', 'ngay', 'transactiondate'] },
  { key: 'dayPeriod', label: 'Buổi', aliases: ['period', 'buổi', 'buoi'] },
  { key: 'localTime', label: 'Giờ', aliases: ['time', 'giờ', 'gio'] },
  { key: 'kind', label: 'Loại thu chi', aliases: ['kind', 'type', 'loại', 'loai'] },
  { key: 'currency', label: 'Tiền tệ', aliases: ['currency', 'tiền tệ', 'tien te'] },
  { key: 'category', label: 'Danh mục', aliases: ['category', 'danh mục', 'danh muc'] },
] as const;

interface Props {
  csrfToken: string;
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onPreview: (draft: DraftPreview) => void;
  onError: (message: string) => void;
}

export default function CsvImportPanel({
  csrfToken,
  busy,
  onBusyChange,
  onPreview,
  onError,
}: Props) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [duplicateDetected, setDuplicateDetected] = useState(false);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());

  async function chooseFile(nextFile: File | null) {
    setFile(nextFile);
    setHeaders([]);
    setMapping({});
    setDuplicateDetected(false);
    setAllowDuplicate(false);
    setIdempotencyKey(crypto.randomUUID());
    onError('');
    if (!nextFile) return;
    if (nextFile.size > 5 * 1024 * 1024) {
      onError(label('Tệp CSV phải nhỏ hơn hoặc bằng 5 MiB.'));
      return;
    }
    try {
      const text = await nextFile.slice(0, 16_384).text();
      const parsedHeaders = parseCsvHeader(text);
      setHeaders(parsedHeaders);
      setMapping(guessMapping(parsedHeaders));
    } catch {
      onError(label('Không đọc được hàng tiêu đề CSV. Hãy kiểm tra định dạng UTF-8.'));
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !mapping.amount || !mapping.description) return;
    onBusyChange(true);
    onError('');
    try {
      onPreview(await uploadCsv(file, mapping, csrfToken, allowDuplicate, idempotencyKey));
    } catch (reason) {
      if (reason instanceof ApiError && reason.statusCode === 409 && !allowDuplicate) {
        setDuplicateDetected(true);
        onError(label('Tệp CSV đã tồn tại. Mở bản nháp cũ hoặc xác nhận nhập lại.'));
      } else {
        onError(reason instanceof Error ? reason.message : label('Không thể nhập tệp CSV.'));
      }
    } finally {
      onBusyChange(false);
    }
  }

  return (
    <section className="panel csv-import-panel" aria-labelledby="csv-import-title">
      <div className="panel-heading">
        <div>
          <h2 id="csv-import-title">{label('Nhập từ bảng tính')}</h2>
          <p className="panel-description">
            {label('Chọn cột để xem trước từng dòng trước khi ghi sổ.')}
          </p>
        </div>
      </div>
      <form className="csv-import-form" onSubmit={(event) => void submit(event)}>
        <label className="field" htmlFor="csv-file">
          <span>{label('Tệp CSV (tối đa 5 MiB)')}</span>
          <input
            id="csv-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => void chooseFile(event.currentTarget.files?.[0] ?? null)}
          />
        </label>
        {file && headers.length > 0 && (
          <div className="csv-mapping-grid">
            {FIELD_OPTIONS.map((field) => (
              <label className="field" key={field.key}>
                <span>
                  {label(field.label)}
                  {'required' in field && field.required ? ' *' : ''}
                </span>
                <select
                  value={mapping[field.key] ?? ''}
                  required={'required' in field && field.required}
                  onChange={(event) => {
                    const column = event.currentTarget.value;
                    setMapping((current) => {
                      const next = { ...current };
                      if (column) next[field.key] = column;
                      else delete next[field.key];
                      return next;
                    });
                    setDuplicateDetected(false);
                    setAllowDuplicate(false);
                    setIdempotencyKey(crypto.randomUUID());
                  }}
                >
                  <option value="">{label('Không dùng cột này')}</option>
                  {headers.map((header) => (
                    <option value={header} key={`${field.key}:${header}`}>
                      {header}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
        {duplicateDetected && (
          <label className="csv-duplicate-consent">
            <input
              type="checkbox"
              checked={allowDuplicate}
              onChange={(event) => {
                setAllowDuplicate(event.currentTarget.checked);
                setIdempotencyKey(crypto.randomUUID());
              }}
            />
            <span>{label('Tôi chủ động muốn nhập lại tệp này thành một bản nháp riêng.')}</span>
          </label>
        )}
        <div className="entry-actions">
          <p>
            {file
              ? `${file.name} · ${file.size.toLocaleString('en-US')} bytes`
              : label('Chỉ nhận tệp CSV UTF-8.')}
          </p>
          <button
            className="primary-button"
            type="submit"
            disabled={
              busy ||
              !file ||
              !mapping.amount ||
              !mapping.description ||
              (duplicateDetected && !allowDuplicate)
            }
          >
            {label(busy ? 'Đang đọc tệp…' : 'Tạo bản xem trước')}
          </button>
        </div>
      </form>
    </section>
  );
}

function parseCsvHeader(input: string): string[] {
  const source = input.replace(/^\uFEFF/, '');
  const headers: string[] = [];
  let field = '';
  let quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === '"' && quoted && source[index + 1] === '"') {
      field += '"';
      index += 1;
    } else if (character === '"') {
      quoted = !quoted;
    } else if (character === ',' && !quoted) {
      headers.push(field.trim());
      field = '';
    } else if ((character === '\n' || character === '\r') && !quoted) {
      break;
    } else {
      field += character;
    }
  }
  if (quoted) throw new Error('Unclosed CSV header quote.');
  headers.push(field.trim());
  if (
    headers.length < 2 ||
    headers.some((header) => !header) ||
    new Set(headers).size !== headers.length
  ) {
    throw new Error('CSV header is invalid.');
  }
  return headers;
}

function guessMapping(headers: string[]): Record<string, string> {
  const normalized = headers.map((header) => header.toLocaleLowerCase('vi-VN'));
  return Object.fromEntries(
    FIELD_OPTIONS.flatMap((field) => {
      const index = normalized.findIndex((header) =>
        field.aliases.some((alias) => alias.toLocaleLowerCase('vi-VN') === header),
      );
      const header = headers[index];
      return header ? [[field.key, header]] : [];
    }),
  );
}
