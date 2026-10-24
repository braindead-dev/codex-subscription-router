# Repair a stale history index

A fork can fail with `durable rollout shrank before projection` when SQLite's
history cursor extends past the end of a rollout file. Moving the same chat
can stay blocked by `the chat's history has not settled yet`. Waiting cannot
make an index cursor move backwards.

Inspect one chat without changing anything:

```sh
python3 scripts/repair_history.py --home <account-codex-home> --thread <thread-id>
```

Close the app and any other Codex processes using that account before applying
a repair. The tool checks open database handles and refuses an active account.
Use the native `codex.real` executable, not the router wrapper:

```sh
python3 scripts/repair_history.py --home <account-codex-home> --thread <thread-id> \
  --apply --codex '<app>/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex.real'
```

The tool backs up the history database under the account home's
`router-repair-backups/`, records the affected streams, and removes only their
four derived index tables' rows. Codex prepares an ephemeral fork to rebuild
the source index from its rollout, without saving a new chat or running a
model turn. The tool verifies that the rebuilt cursors cover their files.
It does not edit rollout files or recover content already missing from them;
the backup preserves the previous index. If rebuilding fails, its error names
the preserved backup. Reopen the app after successful repair.
