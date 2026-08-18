"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
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
 *
 * Two ways out, and they are not the same thing:
 *
 * The × in the corner is chrome. It is always there, it never scrolls away, and
 * it means "put this back". A `footer` is for a decision - "Cancel" beside
 * "Create campaign" means *abandon what I typed*, which is worth a word.
 *
 * The corner used to be a button reading "Close", so a form dialog offered
 * "Close" in the header and "Cancel" in the footer: two text buttons, two verbs
 * for one outcome, and nothing to tell you which was which. An icon does not
 * compete with a verb. It also means a dialog that decides nothing needs no
 * footer at all - the × is the way out, and a lone "Close" down there was only
 * the same button written twice.
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
              {/* Labelled rather than lettered: the glyph is for the eye and
                  the name is for everything else. */}
              <Button variant="quiet" size="sm" iconOnly aria-label={t("common.close")}>
                <X size={16} aria-hidden="true" />
              </Button>
            </RadixDialog.Close>
          </header>
          {/* The body scrolls, the head and the foot do not. The panel caps
              its own height, and without somewhere for the overflow to go a
              long form was simply cut off at the bottom - which Campaign
              settings became the moment it took the posting policy on. */}
          <div className="ui-dialog-body">{children}</div>
          {footer && <footer className="ui-dialog-foot">{footer}</footer>}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
