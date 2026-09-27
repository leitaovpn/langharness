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
It is not yet the complete application-level acceptance: the repository's
`SandboxProvider` wrapper and Node/exec workspace mounts still need to be run
inside this Linux environment with the project's full test dependencies.
