"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { Button } from "./button";

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
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="ui-dialog-overlay" />
        <RadixDialog.Content className="ui-dialog">
          <header className="ui-dialog-head">
            <div>
              <RadixDialog.Title>{title}</RadixDialog.Title>
              {description && (
                <RadixDialog.Description>{description}</RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close asChild>
              <Button variant="quiet" size="sm">Close</Button>
            </RadixDialog.Close>
          </header>
          {children}
          {footer && <footer className="ui-dialog-foot">{footer}</footer>}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
