---
description: Sign in to exactory or create an account from this session with a code sent to your email, and store the API key locally. Use when a command needs a key and none is found, or when the user asks to log in, sign up, register, or get an API key.
---

# Log in to exactory

`exactory login` is on PATH while this plugin is enabled. It stores the key in
`$XDG_CONFIG_HOME/exactory/credentials.json` (default
`~/.config/exactory/credentials.json`), readable by the user only. Every other
`exactory` command reads the key from that file. `EXACTORY_API_KEY`, when set, wins.

The emailed code is the credential. A new address becomes an account. An existing
address gets one more key for the same account.

For a guided first-time setup, with the choice between this flow and the web
sign-up page, use `/exactory:init` instead.

## Procedure

1. Ask the user for the email address to use. Do not guess one.
2. Run `exactory login --email <address>`. exactory sends a six-digit code to that
   address. The command prints the next command to run.
3. Show the user these sentences, then ask for the code:
   "When you use the code, you agree to the Terms of Service
   (https://www.exactory.ai/policies/terms) and the Privacy Policy
   (https://www.exactory.ai/policies/privacy). Paste the code from the email."
4. Run `exactory login --email <address> --code <code>`. On success it prints the
   key's label and the file it was saved to. It never prints the key. Do not ask
   the user for the key, and do not read the file out.
5. If the command says the code is wrong or expired, run step 2 again for a new
   code. If it says to wait, wait one minute before step 2.

`--label <text>` names the key. The default is `plugin on <host name>`.

## After login

- The display name is the part of the address before `@`. The user can change it
  at https://www.exactory.ai/account.
- https://www.exactory.ai/keys lists and revokes keys.
- `exactory logout` removes the local file. The key stays valid on the server
  until it is revoked there.
