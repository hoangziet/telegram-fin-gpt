import { useState } from 'react';
import { ApiError } from '../api/client';
import { uploadImage, type DraftPreview } from '../api/finances';

interface Props {
  csrfToken: string;
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onPreview: (draft: DraftPreview) => void;
  onError: (message: string) => void;
}

export default function ImageImportPanel({
  csrfToken,
  busy,
  onBusyChange,
  onPreview,
  onError,
}: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [duplicateDetected, setDuplicateDetected] = useState(false);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    onBusyChange(true);
    onError('');
    try {
      onPreview(await uploadImage(file, csrfToken, allowDuplicate, idempotencyKey));
    } catch (reason) {
      if (reason instanceof ApiError && reason.statusCode === 409 && !allowDuplicate) {
        setDuplicateDetected(true);
        onError('This image was already imported. Choose duplicate import only if intended.');
      } else {
        onError(reason instanceof Error ? reason.message : 'Could not read the receipt image.');
      }
    } finally {
      onBusyChange(false);
    }
  }

  return (
    <section className="panel csv-import-panel" aria-labelledby="image-import-title">
      <div className="panel-heading">
        <div>
          <h2 id="image-import-title">Import a receipt image</h2>
          <p className="panel-description">
            Create one transaction for the receipt total. Review the preview before recording.
          </p>
        </div>
      </div>
      <form className="csv-import-form" onSubmit={(event) => void submit(event)}>
        <label className="field" htmlFor="image-file">
          <span>JPEG, PNG or WebP image (up to 10 MiB)</span>
          <input
            id="image-file"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(event) => {
              const nextFile = event.currentTarget.files?.[0] ?? null;
              setFile(nextFile);
              setDuplicateDetected(false);
              setAllowDuplicate(false);
              setIdempotencyKey(crypto.randomUUID());
              onError('');
              if (nextFile && nextFile.size > 10 * 1024 * 1024) {
                setFile(null);
                onError('The image must be 10 MiB or smaller.');
              }
            }}
          />
        </label>
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
            <span>I want to create another draft from this image.</span>
          </label>
        )}
        <div className="entry-actions">
          <p>
            {file
              ? `${file.name} · ${file.size.toLocaleString('en-US')} bytes`
              : 'Choose a receipt image.'}
          </p>
          <button
            className="primary-button"
            type="submit"
            disabled={busy || !file || (duplicateDetected && !allowDuplicate)}
          >
            {busy ? 'Reading image…' : 'Create preview'}
          </button>
        </div>
      </form>
    </section>
  );
}
