---
description: Set up exactory in this session - check what credential already exists, then register or sign in, either fully in the session with an emailed code or through the web sign-up page in the browser. Use when the user is new, says init, set up, get started, or register, or when a first command finds no key.
---

# Set up exactory

The goal of this skill is one working credential in
`$XDG_CONFIG_HOME/exactory/credentials.json` (default `~/.config/exactory/`), stored
by `exactory login`. Every `exactory` command reads it from there.
`EXACTORY_API_KEY`, when set, wins over the file.

Never ask the user to paste an API key or a password into the chat.

## Step 1: Check what already exists

Run `exactory whoami`.

- `"source": "environment"` - the key comes from `EXACTORY_API_KEY`. Go to step 2.
- `"source": "file"` - a stored key exists. Go to step 2 and report the email and
  label it prints.
- `"source": "none"` - nothing is set. Go to step 3.

## Step 2: Confirm the existing key works, then stop

Run `exactory tasks --limit 1`.

- Success: tell the user they are already set up (with the email and label when the
  file holds them), and stop. To switch accounts: `exactory logout`, then step 3.
- A 401 error: the key is stale. Say so. If the source is the environment, ask the
  user to unset or fix `EXACTORY_API_KEY`. If the source is the file, continue with
  step 3 to replace it.

## Step 3: One question, two ways in

Ask the user which they want. Both end with the same stored key; only the entrance
differs.

1. **In this session** (recommended): a six-digit code sent to their email. About a
   minute, no browser. A new address becomes an account; an existing address signs in.
2. **In the browser**: create the account on the exactory sign-up page with a
   password, Google, or GitHub. Then return here for one code that gives this
   session its key.

## Step 4a: In this session

Follow the procedure in the `login` skill: ask for the email address, run
`exactory login --email <address>`, show the terms sentence, ask for the code from
the email, run `exactory login --email <address> --code <code>`.

## Step 4b: In the browser

1. Run `exactory open-signup`. It opens the default browser at the sign-up page and
   prints the URL. If it reports `"opened": false`, give the user the URL to open
   themselves.
2. Tell the user to finish the sign-up there - including the email confirmation the
   page asks for - and to say so here when done.
3. Continue with step 4a **using the same email address**. The code signs into the
   account they just created; it does not make a second one.

## Step 5: Confirm and close

1. Run `exactory tasks --limit 1`. Success proves the stored key works end to end.
2. Tell the user, in two or three lines: where the key is stored, that
   https://www.exactory.ai/console/keys lists and revokes keys, and that
   `exactory logout` removes the local key. Their display name defaults to the part
   of the address before `@`, changeable at https://www.exactory.ai/console/account.

If any step fails twice in a row, stop and show the user the exact error text
instead of retrying further.
