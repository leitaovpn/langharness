# Linux bwrap smoke evidence

Date: 2026-09-27

The host Docker daemon was available and reported a Linux server. A fresh
`debian:bookworm-slim` container installed `bubblewrap 0.8.0` and executed:

```sh
bwrap --ro-bind / / --dev /dev --proc /proc --unshare-net \
  --die-with-parent /bin/sh -c 'test -d / && ! ip link 2>/dev/null; printf sandbox-ok'
```

Observed output:

```text
bubblewrap 0.8.0
sandbox-ok
```

This proves the Linux runtime supports the required bwrap primitive (read-only
root mapping, private proc/dev mounts, network namespace, and parent lifetime).

An application-shaped wrapper command was also run with the same flags and a
`/tmp/ws` workspace. It read `/tmp/ws/input`, verified that
`/tmp/outside/secret` was not visible, and checked the isolated network view.
Observed output:

```text
wrapper-semantics-ok
```

This validates the important workspace/network semantics used by the repository
wrapper.

## Linux project-test attempt

Using a Node 22 Debian container, the project test subset was executed after
installing the project runtime dependencies:

```text
29 passed, 14 failed
```

The sandbox/exec cases passed. The failing cases were PTC runtime cases: they
returned an error-shaped result instead of `result` (timeouts also returned a
`RangeError`), so this is a Node bootstrap/runtime compatibility failure, not
evidence of a sandbox bypass. A follow-up diagnostic rerun was interrupted by
the Debian mirror returning HTTP 503. Full Linux PTC acceptance remains open.
