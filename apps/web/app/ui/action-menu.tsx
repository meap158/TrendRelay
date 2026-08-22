"use client";

import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { ChevronDown } from "lucide-react";

import { buttonClass, type ButtonSize, type ButtonVariant } from "./button";

export type ActionMenuItem = {
  id: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
};

/**
 * A compact menu of commands.
 *
 * This is deliberately not a select: commands do not become a stored value.
 * The trigger keeps native button semantics, the panel uses menu/menuitem,
 * Arrow/Home/End move focus, and Escape returns focus to the trigger.
 */
export function ActionMenu({
  label,
  icon,
  ariaLabel = label,
  items,
  onSelect,
  disabled,
  variant = "secondary",
  size = "sm",
}: {
  label: string;
  icon?: ReactNode;
  ariaLabel?: string;
  items: readonly ActionMenuItem[];
  onSelect: (id: string) => void;
  disabled?: boolean;
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  const menuId = useId();
  const wrapper = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [open, setOpen] = useState(false);
  const [panelBox, setPanelBox] = useState({ left: 0, top: 0, width: 320 });

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  function openAt(index: number) {
    showMenu();
    queueMicrotask(() => itemRefs.current[index]?.focus());
  }

  function showMenu() {
    const rect = trigger.current?.getBoundingClientRect();
    if (rect) {
      const gutter = 14;
      // `innerWidth` includes a classic scrollbar on Windows. The document's
      // client width is the actual painted lane, so using it prevents the last
      // pixel of the panel from sitting under that scrollbar.
      const viewportWidth = document.documentElement.clientWidth;
      const width = Math.min(320, viewportWidth - gutter * 2);
      setPanelBox({
        left: Math.max(gutter, Math.min(rect.right - width, viewportWidth - width - gutter)),
        top: rect.bottom + 5,
        width,
      });
    }
    setOpen(true);
  }

  function move(event: KeyboardEvent<HTMLDivElement>, direction: 1 | -1) {
    event.preventDefault();
    const current = itemRefs.current.findIndex((item) => item === document.activeElement);
    const next = current < 0
      ? direction > 0 ? 0 : items.length - 1
      : (current + direction + items.length) % items.length;
    itemRefs.current[next]?.focus();
  }

  return (
    <div className="ui-action-menu" ref={wrapper}>
      <button
        ref={trigger}
        type="button"
        className={buttonClass({ variant, size })}
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-controls={menuId}
        aria-expanded={open}
        disabled={disabled}
        onClick={() => {
          if (open) setOpen(false);
          else showMenu();
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            openAt(0);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            openAt(items.length - 1);
          }
        }}
      >
        {icon}
        {label}
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div
          id={menuId}
          className="ui-action-menu-panel"
          style={panelBox}
          role="menu"
          aria-label={ariaLabel}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") move(event, 1);
            else if (event.key === "ArrowUp") move(event, -1);
            else if (event.key === "Home") {
              event.preventDefault();
              itemRefs.current[0]?.focus();
            } else if (event.key === "End") {
              event.preventDefault();
              itemRefs.current[items.length - 1]?.focus();
            } else if (event.key === "Tab") {
              setOpen(false);
            }
          }}
        >
          {items.map((item, index) => {
            const detail = item.disabled ? item.disabledReason ?? item.description : item.description;
            return (
              <button
                key={item.id}
                ref={(node) => { itemRefs.current[index] = node; }}
                type="button"
                role="menuitem"
                aria-disabled={item.disabled || undefined}
                title={detail}
                onClick={() => {
                  if (item.disabled) return;
                  setOpen(false);
                  onSelect(item.id);
                }}
              >
                {item.icon && <span className="ui-action-menu-icon">{item.icon}</span>}
                <span>
                  <strong>{item.label}</strong>
                  {detail && <small>{detail}</small>}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
