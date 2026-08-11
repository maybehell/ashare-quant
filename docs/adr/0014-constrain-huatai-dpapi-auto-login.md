# ADR 0014: Constrain Huatai DPAPI automatic login

Status: accepted

Date: 2026-08-11

## Context

ADR 0005 deliberately stopped unattended read-only refresh at the Huatai
trading login page. The operator has now explicitly requested local automatic
login so an expired desktop session does not prevent account observation.
Authentication automation handles a sensitive credential and confirmation
control, but it must not acquire order, ledger, or account-import authority.

## Decision

1. Automatic login is a separate broker-boundary capability owned by
   `huatai_login.py`; it does not import or call order submission code.
2. Configuration must explicitly enable it. Credentials are read only at the
   login attempt from the configured Windows current-user DPAPI file.
3. Enrollment accepts the password only through a hidden interactive prompt;
   YAML, command-line password arguments, environment variables, logs, and
   audit artifacts are not credential sources.
4. A login attempt requires one SHA-256-pinned `hexin.exe`, one same-directory
   `xiadan.exe`, one visible login root, and one exact account, password, and
   login-control signature. The account field must read back exactly before the
   password is entered.
5. Each `broker-refresh` may click the exact `登录` control at most once. It may
   poll read-only snapshots for bounded confirmation but never clicks again.
6. Unexpected input controls, captcha, second-factor prompts, window/process
   ambiguity, missing credentials, readback mismatch, timeout, or UI changes
   fail closed and require manual login.
7. The audit records status, attempt and click counts, credential-source class,
   and a redacted message. It never records account ID or password.
8. Login cannot initialize a live ledger, import account state, create an order,
   navigate an order form, submit/cancel, clear the emergency stop, enable
   broker submission, or grant Canary authority.

This supersedes ADR 0005 only where ADR 0005 requires credentials and login
confirmation to remain manual. All other recovery constraints remain in force.

## Consequences

Positive:

- A scheduled read-only refresh can recover one ordinary expired login session.
- Credentials remain bound to the Windows user and absent from normal config.
- Every automatic confirmation remains bounded, redacted, and independently
  auditable.

Costs:

- DPAPI protects local storage but cannot protect credentials after the same
  Windows user or desktop session is compromised.
- Captcha, MFA, client updates, or control-ID changes still require manual
  intervention.
- Configuration changes invalidate stale live certification and require a
  separate re-certification before any future submission review.

## Alternatives considered

### Store the password in YAML or pass it on the command line

Rejected because config, process listings, shell history, and logs are broader
and less controlled disclosure surfaces.

### Retry login until it succeeds

Rejected because it risks lockout, duplicate confirmation, and automation
against an unclassified broker state.

### Reuse the order RPA driver

Rejected because authentication and order-entry capabilities must remain
separate and independently reviewable.

### Handle captcha or second-factor prompts automatically

Rejected because these states require explicit human authentication and are
not part of ordinary session recovery.
