/**
 * C-end Dialog — clean white style, never affected by dark mode.
 * All C-end dialogs MUST use these components instead of ui/dialog.
 * Do NOT use in B-end (ADMIN_PREFIX) pages.
 */
'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/* ── C-end design tokens ── */
const OVERLAY = 'fixed inset-0 z-50 bg-black/30 backdrop-blur-sm';
const CONTENT =
  'fixed left-[50%] top-[50%] z-50 grid w-full max-w-xl translate-x-[-50%] translate-y-[-50%] gap-0 rounded-2xl bg-white border border-[#E8E8E6] shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[state=closed]:slide-out-to-left-1/2 data-[state=closed]:slide-out-to-top-[48%] data-[state=open]:slide-in-from-left-1/2 data-[state=open]:slide-in-from-top-[48%]';
const HEADER =
  '-mx-6 -mt-6 p-5 border-b border-[#E8E8E6] flex flex-col space-y-1.5 text-center sm:text-left';
const FOOTER =
  '-mx-6 -mb-6 p-5 border-t border-[#E8E8E6] flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-3';
const TITLE = 'text-base font-semibold leading-tight text-[#1A1A1A]';
const DESCRIPTION = 'text-sm text-[#8A8A8A]';
const CLOSE_BTN =
  'absolute right-3 top-3 p-1.5 rounded-lg text-[#B0B0B0] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] transition-colors';

/* ── Dialog ── */

const CDialog = DialogPrimitive.Root;
const CDialogTrigger = DialogPrimitive.Trigger;
const CDialogPortal = DialogPrimitive.Portal;
const CDialogClose = DialogPrimitive.Close;

const CDialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(OVERLAY, className)}
    {...props}
  />
));
CDialogOverlay.displayName = 'CDialogOverlay';

const CDialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <CDialogPortal>
    <CDialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      aria-describedby={undefined}
      className={cn(CONTENT, className)}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className={CLOSE_BTN}>
        <X className="h-4 w-4" />
        <span className="sr-only">Close</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </CDialogPortal>
));
CDialogContent.displayName = 'CDialogContent';

const CDialogHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn(HEADER, className)} {...props} />
);
CDialogHeader.displayName = 'CDialogHeader';

const CDialogFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn(FOOTER, className)} {...props} />
);
CDialogFooter.displayName = 'CDialogFooter';

const CDialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn(TITLE, className)}
    {...props}
  />
));
CDialogTitle.displayName = 'CDialogTitle';

const CDialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn(DESCRIPTION, className)}
    {...props}
  />
));
CDialogDescription.displayName = 'CDialogDescription';

export {
  CDialog,
  CDialogClose,
  CDialogContent,
  CDialogDescription,
  CDialogFooter,
  CDialogHeader,
  CDialogOverlay,
  CDialogPortal,
  CDialogTitle,
  CDialogTrigger,
};
