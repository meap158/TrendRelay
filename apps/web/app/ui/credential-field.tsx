"use client";

/**
 * A saved credential: what is stored, and a box to replace it.
 *
 * The box used to render empty whether or not a key was saved. An empty box
 * reads as "nothing here", so the reflex is to paste the key again - and when
 * five settings are saved and one of them is wrong, an operator has no way to
 * see which one, because they all look equally blank. That is how R2 ended up
 * with an account ID in the access-key field and nobody could tell.
 *
 * So the saved value is shown, masked to its last few characters: enough to
 * recognise which key is in place, never enough to use. The eye asks the API
 * for the value in full, which it will only do over loopback, for a role that
 * could overwrite the key anyway, and only for keys these screens can write.
 *
 * The mask is deliberately never inside the input. The input holds a
 * replacement and nothing else, so there is no path where a row of dots is
 * submitted and saved over a working credential.
 */

import { useState } from "react";

export type CredentialFieldValue = {
  id: string;
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  configured: boolean;
  /** The saved value with all but its last few characters hidden. */
  preview?: string | null;
};

export function CredentialRow({
  field,
  value,
  disabled,
  onChange,
  onReveal,
  labels,
}: {
  field: CredentialFieldValue;
  value: string;
  disabled?: boolean;
  onChange: (next: string) => void;
  /** Fetches the value in full, or null if it cannot be read. */
  onReveal: (key: string) => Promise<string | null>;
  labels: {
    configured: string;
    required: string;
    optional: string;
    notSet: string;
    reveal: string;
    hide: string;
    replace: string;
    paste: string;
  };
}) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    if (revealed) {
      setRevealed(null);
      return;
    }
    setBusy(true);
    try {
      setRevealed(await onReveal(field.key));
    } finally {
      setBusy(false);
    }
  }

  return (
    <label className="credential-field">
      <span>
        {field.label}
        <b className={field.configured ? "configured" : "missing"}>
          {field.configured
            ? labels.configured
            : field.required ? labels.required : labels.optional}
        </b>
      </span>

      {/* What is stored, above the box that would replace it. */}
      <span className="credential-saved">
        <code className={revealed ? "revealed" : ""}>
          {revealed ?? field.preview ?? labels.notSet}
        </code>
        {field.configured && (
          <button
            type="button"
            className="credential-eye"
            aria-pressed={Boolean(revealed)}
            aria-label={revealed ? labels.hide : labels.reveal}
            title={revealed ? labels.hide : labels.reveal}
            disabled={disabled || busy}
            onClick={() => void toggle()}
          >
            {revealed ? <EyeOff /> : <Eye />}
          </button>
        )}
      </span>

      <input
        autoComplete={field.secret ? "new-password" : "off"}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        placeholder={field.configured ? labels.replace : labels.paste}
        spellCheck={false}
        type={field.secret ? "password" : "text"}
        value={value}
      />
      <small>{field.help}</small>
    </label>
  );
}

function Eye() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

function EyeOff() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6.3 3.8A6.6 6.6 0 0 1 8 3.5C12 3.5 14.5 8 14.5 8a12 12 0 0 1-2 2.5" />
      <path d="M3.9 4.9A12 12 0 0 0 1.5 8S4 12.5 8 12.5a6.6 6.6 0 0 0 2-.3" />
      <path d="M2 2l12 12" />
    </svg>
  );
}
