# ADR 0015: Constrain Huatai post-login notice dismissal

Status: accepted

Date: 2026-08-11

## Context

After a successful Huatai desktop login, the client can display a navigation
dialog containing branch announcements and security settings. The dialog does
not prevent the current account snapshot from being read, but it disables or
obscures navigation controls and can block read-only preview and monitor
certification.

Closing an arbitrary dialog, pressing Enter or Escape, or clicking screen
coordinates would be unsafe inside a broker client. The recovery action must be
strictly narrower than order navigation and independently auditable.

## Decision

1. Post-login notice dismissal is a separate broker-boundary capability owned
   by `huatai_notice.py`; it does not import login credentials, order RPA,
   ledger, or submission modules.
2. Configuration must explicitly enable the action.
3. The driver requires one SHA-256-pinned `hexin.exe`, one same-directory
   `xiadan.exe`, and exactly one visible `xiadan.exe` `#32770` dialog matching
   all four markers: `导航`, `没有新消息，下次不再显示`, `今日不再提示`, and one
   `CCustomTabCtrl`.
4. The driver sends exactly one `WM_CLOSE` to that dialog handle and confirms
   that the window is no longer visible within a bounded timeout.
5. It never clicks either notice preference, never uses Enter, Escape, generic
   close-button discovery, or screen coordinates, and never records notice
   body text.
6. Absence is a normal no-op. Ambiguous dialogs, contract mismatch, or failed
   close confirmation are audited as blocked and prevent subsequent desktop
   Canary navigation for that refresh.
7. Dismissal grants no order, ledger, account-import, emergency-stop,
   submission, or Canary authority.

## Consequences

Positive:

- A normal post-login announcement no longer leaves the trading navigation
  disabled for read-only automation.
- Important notice preferences remain under operator control.
- Changed or unexpected dialogs fail closed without a generic keyboard action.

Costs:

- A Huatai dialog layout or marker change blocks dismissal until the contract is
  reviewed and updated.
- The configuration change invalidates stale desktop certification and requires
  separate re-certification before any future submission review.

## Alternatives considered

### Automatically select a do-not-show-again preference

Rejected because it changes an operator communication preference and could hide
future broker notices.

### Press Escape or Enter after every login

Rejected because focus can move and those keys may confirm an unrelated dialog
or action.

### Click the visible X by coordinates

Rejected because layout, scaling, focus, and overlapping windows make a screen
coordinate an unstable broker identity.

### Ignore the notice

Rejected because it leaves trading navigation disabled and makes later preview
failures misleading.
