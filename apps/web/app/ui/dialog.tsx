"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { Button } from "./button";
import { useT } from "../i18n-provider";

/**
 * A modal panel.
 *
 * Radix supplies the parts that are easy to get wrong by hand and easy to
 * forget entirely: the focus trap, restoring focus to whatever opened it,
 * marking the rest of the page inert for screen readers, and closing on Escape
 * or an outside click. The hand-rolled version this replaced only had Escape.
 */
export function Dialog({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  size = "default",
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /**
   * `wide` for a panel whose content is the point rather than a form — a
   * gallery of objects, a stack of effects with their own controls. At the
   * default width those get a column each of about three hundred pixels, and a
   * grid of pictures in one of those is a scrollbar with a few thumbnails
   * behind it.
   */
  size?: "default" | "wide";
}) {
  const t = useT();
  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="ui-dialog-overlay" />
        <RadixDialog.Content
          className={size === "wide" ? "ui-dialog ui-dialog-wide" : "ui-dialog"}
        >
          <header className="ui-dialog-head">
            <div>
              <RadixDialog.Title>{title}</RadixDialog.Title>
              {description && (
                <RadixDialog.Description>{description}</RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close asChild>
              <Button variant="quiet" size="sm">{t("common.close")}</Button>
            </RadixDialog.Close>
          </header>
          {children}
          {footer && <footer className="ui-dialog-foot">{footer}</footer>}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
