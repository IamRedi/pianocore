# Pi-connected installable client and product presentation

Status: proposed for review · 16 November 2026.

Owner requested the current crystal identity across website, portfolio and GitHub, and clarified that the installed app must connect to their Pi.

The manifest preserves the legacy implicit `/` app identity and defines `/ui/` scope and start URL. This avoids creating a duplicate app when the previous manifest is updated. Browser-native installation is offered only after a real `beforeinstallprompt` on a secure origin. Safari/manual installation is explained in Settings → About and in the four-language native presentation. Installation never creates a second musical engine. The Pi must remain reachable. No service worker is introduced: ADR-022's no-interception contract and fresh same-origin API semantics remain intact.

The presentation ships under the exact UI asset allowlist and is mirrored to the public showcase and portfolio. It does not probe, proxy or fetch any device. Public users explicitly enter their own Pi address; it is not persisted. Plain HTTP navigation requires an additional explicit link after explaining the installation limit. The website itself is not the installed piano client.

Auto appearance follows the operating-system preference. Manual Day/Night choices remain browser-local with the existing preference key.

No new authentication, network exposure, HTTPS provisioning, appliance deployment, musical behavior or physical acceptance is authorized by this implementation record. The observed current appliance entry was HTTP. End-to-end installation on the owner's Pi therefore remains unverified until a secure app origin is available and a controlled release is approved.
