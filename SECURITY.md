# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem: an issue is disclosed the
moment it is filed, before anyone has had a chance to fix it.

Report it privately through GitHub's vulnerability reporting form:

**<https://github.com/EmadMokhtar/skill-evaluator/security/advisories/new>**

Include what you can of: the version affected, how to reproduce it, and what an attacker
could do with it. You will get an acknowledgement in the advisory thread, and the fix — or
the reasoning if it is not treated as a vulnerability — is discussed there before anything
is made public. Credit is given in the advisory unless you ask otherwise.

## Supported versions

skill-lens is `0.x`. Fixes go into the next release from `main`, which is published to
PyPI automatically on merge; there are no maintenance branches for earlier versions. Pin
what you depend on and upgrade to pick up a fix.

## What is checked automatically

Every pull request, every push to `main`, a weekly schedule, and the release gate all run a
dependency audit against the lockfile; the release also attaches a Software Bill of
Materials and PyPI uploads carry signed attestations. The full list, with how to run each
check locally, is in the
[Security](https://emadmokhtar.github.io/skill-evaluator/security/) page of the docs.
