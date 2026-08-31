# LAN Firewall Guide

The only permitted inbound rule is TCP gateway port 8080 on the Windows Private profile.

```powershell
.\scripts\configure_lan_firewall.ps1 -Action Show
.\scripts\configure_lan_firewall.ps1 -Action Add -Port 8080 -Confirmed
.\scripts\configure_lan_firewall.ps1 -Action Remove -Confirmed
```

The script requires administrator rights for changes, refuses to add a rule without an active Private profile and leaves unrelated firewall rules untouched.

Never open 5173, 8000, 5432, 9000, 9001, 2375 or 2376. Never disable Windows Firewall.
