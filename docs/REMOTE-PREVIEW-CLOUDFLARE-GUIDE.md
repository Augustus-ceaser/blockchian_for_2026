# Controlled Remote Preview With Cloudflare

Remote preview is optional and manually gated.

1. Create a Cloudflare Access self-hosted application and restrict it to invited identities.
2. Verify an unauthenticated browser is denied.
3. Create the named Tunnel and published hostname only after Access protection exists.
4. Point the Tunnel origin to the loopback gateway.
5. Store cloudflared configuration in ignored `config/remote-preview.local.yml`.
6. Set the protected HTTPS `MEDTRUST_PUBLIC_ORIGIN`.
7. Start with `.\scripts\start_remote_preview.ps1 -AccessProtectionConfirmed`.
8. Stop with `.\scripts\stop_remote_preview.ps1` after the review.

The scripts do not install cloudflared, sign into an account, create DNS, create a Quick Tunnel or print a Tunnel token. No formal remote preview is allowed without Access protection.

Use demonstration data only. Do not record real credentials, domains, email addresses or tunnel tokens in Git or delivery documents.
